"""
Test suite validating reverse export of complex 3D RC Seismic Frame models
(Gravity, ELF seismic, Mass Source, and RSA) to ETABS (.e2k) and verifying roundtrip integrity.
"""

from pathlib import Path
import sys
import pytest

# Ensure examples folder is importable
_examples_dir = str(Path(__file__).resolve().parent.parent / "examples")
if _examples_dir not in sys.path:
    sys.path.insert(0, _examples_dir)

from fem3d import Results, model_from_core
from struct_core import validate_structural_model
from struct_link import read_project, write_model
from export_etabs_seismic_frame import build_seismic_frame_project


def test_export_etabs_seismic_model(tmp_path: Path):
    """Verify exporting 3D RC Seismic Frame to native ETABS .e2k format and re-importing."""
    project, seismic_params = build_seismic_frame_project()
    validate_structural_model(project.model)

    # 1. Export model to .e2k
    export_file = tmp_path / "exported_seismic_frame.e2k"
    write_model(project, export_file)
    assert export_file.exists()
    assert export_file.stat().st_size > 5000

    e2k_text = export_file.read_text(encoding="utf-8")
    assert "$ PROGRAM INFORMATION" in e2k_text
    assert 'PROGRAM  "ETABS"  VERSION "23.3.2"' in e2k_text
    assert "$ CONTROLS" in e2k_text
    assert "$ STORIES - IN SEQUENCE FROM TOP" in e2k_text
    assert "$ POINT COORDINATES" in e2k_text
    assert "$ LINE CONNECTIVITIES" in e2k_text
    assert "$ LINE ASSIGNS" in e2k_text
    assert "$ POINT OBJECT LOADS" in e2k_text
    assert "$ FRAME OBJECT LOADS" in e2k_text
    assert "$ MASS SOURCE" in e2k_text
    assert "$ LOAD CASES" in e2k_text
    assert "$ END OF MODEL FILE" in e2k_text
    assert 'SEISMIC "EQx"  "ASCE 7-22"' in e2k_text
    assert 'LC "EQx"' not in e2k_text

    # 2. Re-import model
    re_proj = read_project(export_file)
    validate_structural_model(re_proj.model)
    re_model = re_proj.model

    # 3. Verify counts
    assert len(re_model.nodes) == 18
    assert len(re_model.elements) == 26
    assert len(re_model.supports) == 6
    assert {"DEAD", "SuperDead", "LIVE", "EQx"}.issubset({lc.id for lc in re_model.load_cases})
    eqx_case = next(lc for lc in re_model.load_cases if lc.id == "EQx")
    assert eqx_case.auto_load == "ASCE 7-22"
    assert eqx_case.seismic_params is not None

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

    # 6. Verify ELF Lateral Seismic Base Shear (EQx)
    st_elf = model_from_core(re_proj, load_case_id="EQx", include_shear_deformation=True)
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


def test_read_reference_etabs_model():
    """Verify parsing real-world reference ETABS 23.3.2 exported .e2k file."""
    ref_file = (
        Path(__file__).resolve().parent.parent
        / "examples"
        / "output"
        / "etabs_sap_files"
        / "reference_etabs_model.e2k"
    )
    assert ref_file.exists(), f"Reference file not found: {ref_file}"

    proj = read_project(ref_file)
    validate_structural_model(proj.model)
    model = proj.model

    assert len(model.nodes) > 0
    assert len(model.elements) > 0
    assert len(model.supports) > 0
    assert len(model.materials) > 0
    assert len(model.sections) > 0
    assert len(model.load_cases) > 0
