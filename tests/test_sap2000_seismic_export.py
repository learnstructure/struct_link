"""
Test suite validating reverse export of complex 3D RC Seismic Frame models
(Gravity, ELF seismic, Mass Source, and RSA) to SAP2000 (.s2k) and verifying roundtrip integrity.
"""

from pathlib import Path
import pytest

from fem3d import model_from_core, Results
from struct_core import validate_structural_model
import sys
_examples_dir = str(Path(__file__).resolve().parent.parent / "examples")
if _examples_dir not in sys.path:
    sys.path.insert(0, _examples_dir)

from struct_link import read_project, write_model
from export_sap2000_seismic_frame import build_seismic_frame_project


def test_export_sap2000_seismic_model(tmp_path: Path):
    """Verify exporting 3D RC Seismic Frame to native SAP2000 .s2k format and re-importing."""
    project, seismic_params = build_seismic_frame_project()
    validate_structural_model(project.model)

    # 1. Export model to .s2k
    export_file = tmp_path / "exported_seismic_frame.s2k"
    write_model(project, export_file)
    assert export_file.exists()
    assert export_file.stat().st_size > 5000

    s2k_text = export_file.read_text(encoding="utf-8")
    assert 'TABLE:  "FRAME LOCAL AXES ASSIGNMENTS 1 - TYPICAL"' in s2k_text
    assert "Frame=1   Angle=90   AdvanceAxes=No" in s2k_text

    # 2. Re-import model
    re_proj = read_project(export_file)
    validate_structural_model(re_proj.model)
    re_model = re_proj.model

    # 3. Verify counts
    assert len(re_model.nodes) == 18
    assert len(re_model.elements) == 26
    assert len(re_model.supports) == 6
    assert {"DEAD", "SuperDead", "LIVE", "EQ_X"}.issubset({lc.id for lc in re_model.load_cases})

    # 4. Verify Mass Source
    assert re_model.mass_source is not None
    assert re_model.mass_source.include_element_mass is True
    assert len(re_model.mass_source.factors) == 2

    # 5. Verify Gravity Reactions
    targets = {"DEAD": 288.96, "SuperDead": 700.00, "LIVE": 280.00}
    for lc_id, target in targets.items():
        st = model_from_core(re_proj, load_case_id=lc_id, include_shear_deformation=True)
        st.solve()
        reacs = Results(st).reactions()
        sup_nodes = [nid for nid, n in st.nodes.items() if n.is_supported()]
        fz = float(reacs[reacs["node"].isin(sup_nodes)]["Fz"].sum())
        assert pytest.approx(fz, rel=1e-3) == target

    # 6. Verify ELF Lateral Seismic Base Shear (EQ_X)
    st_elf = model_from_core(re_proj, load_case_id="EQ_X", include_shear_deformation=True)
    st_elf.solve()
    reacs_elf = Results(st_elf).reactions()
    sup_nodes = [nid for nid, n in st_elf.nodes.items() if n.is_supported()]
    fx = abs(float(reacs_elf[reacs_elf["node"].isin(sup_nodes)]["Fx"].sum()))
    assert pytest.approx(fx, rel=1e-3) == 132.37

    # 7. Verify Dynamic Modal Periods
    st_dyn = model_from_core(re_proj, include_shear_deformation=True)
    modal_res = st_dyn.modal_analysis(num_modes=6, lumped=True)
    periods = modal_res["periods"]
    assert pytest.approx(periods[0], rel=1e-3) == 0.457664
    assert pytest.approx(periods[1], rel=1e-3) == 0.416672
    assert pytest.approx(periods[2], rel=1e-3) == 0.387409

    # 8. Verify ASCE 7-22 Response Spectrum Analysis Base Shear
    rsa_res = st_dyn.response_spectrum_analysis(
        params=seismic_params,
        n_modes=12,
        direction="x",
        scale_factor=9.80665,
        combination="CQC",
    )
    vb_rsa = float(rsa_res["combined_base_shear"][0])
    assert pytest.approx(vb_rsa, rel=1e-3) == 818.81
