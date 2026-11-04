"""
Test suite validating end-to-end import and analysis of complex SAP2000 models
containing gravity, ELF seismic, and RSA definitions (frame_ELF_RSA_Timoshenko.s2k).
"""

from pathlib import Path
import pytest

from fem3d import model_from_core, Results
from struct_core import validate_structural_model
from struct_link import S2kParser, read_project
from struct_load import SeismicParameters


def test_import_sap2000_seismic_model():
    ref_path = (
        Path(__file__).resolve().parent.parent
        / "examples"
        / "output"
        / "etabs_sap_files"
        / "frame_ELF_RSA_Timoshenko.s2k"
    )
    assert ref_path.exists(), f"Reference file not found: {ref_path}"

    project = read_project(ref_path)
    validate_structural_model(project.model)
    model = project.model

    # 1. Verification of structural entity counts
    assert len(model.nodes) == 18
    assert len(model.elements) == 26
    assert len(model.supports) == 6
    assert {"DEAD", "Live", "SuperDead", "EQx"}.issubset({lc.id for lc in model.load_cases})
    # CSI frames are Timoshenko members; the SAP2000 reader must match the ETABS reader
    assert all(e.include_shear_deformation for e in model.elements)

    # 2. Verification of Mass Source
    assert model.mass_source is not None
    assert model.mass_source.include_element_mass is True

    # 3. Verification of Gravity Reactions (Timoshenko)
    targets = {"DEAD": 288.96, "Live": 280.00, "SuperDead": 700.00}
    for lc_id, target in targets.items():
        struct_g = model_from_core(project, load_case_id=lc_id, include_shear_deformation=True)
        struct_g.solve()
        reacs = Results(struct_g).reactions()
        sup_nodes = [nid for nid, n in struct_g.nodes.items() if n.is_supported()]
        fz_sum = float(reacs[reacs["node"].isin(sup_nodes)]["Fz"].sum())
        assert pytest.approx(fz_sum, rel=1e-3) == target

    # 4. Verification of ELF Seismic Base Shear (EQx)
    struct_elf = model_from_core(project, load_case_id="EQx", include_shear_deformation=True)
    struct_elf.solve()
    reacs_elf = Results(struct_elf).reactions()
    sup_nodes = [nid for nid, n in struct_elf.nodes.items() if n.is_supported()]
    fx_sum = abs(float(reacs_elf[reacs_elf["node"].isin(sup_nodes)]["Fx"].sum()))
    assert pytest.approx(fx_sum, rel=1e-3) == 128.59

    # 5. Verification of Modal Periods
    struct_dyn = model_from_core(project, include_shear_deformation=True)
    modal_res = struct_dyn.modal_analysis(num_modes=6, lumped=True)
    periods = modal_res["periods"]
    assert pytest.approx(periods[0], rel=1e-3) == 0.457663
    assert pytest.approx(periods[1], rel=1e-3) == 0.416672
    assert pytest.approx(periods[2], rel=1e-3) == 0.387409

    # 6. Verification of RSA Base Shear
    tables = S2kParser.parse_file(ref_path)
    t_spec = tables.get("FUNCTION - RESPONSE SPECTRUM - ASCE 7-22")
    assert t_spec is not None and len(t_spec.rows) > 0
    t_pts = [float(r["Period"]) for r in t_spec.rows]
    sa_pts = [float(r["Accel"]) for r in t_spec.rows]

    rsa_table = struct_dyn.response_spectrum_analysis(
        spectrum_curve=(t_pts, sa_pts),
        n_modes=12,
        direction="x",
        scale_factor=9.8066,
        combination="CQC",
    )
    vb_table = float(rsa_table["combined_base_shear"][0])
    assert pytest.approx(vb_table, rel=1e-3) == 846.384
