"""
Test suite validating member local axis orientation handling
during SAP2000 (.s2k) import, export, and full roundtrip.
"""

from pathlib import Path
import numpy as np
import pytest

from fem3d import model_from_core
from struct_core import (
    BeamElement,
    ElasticMaterial,
    Node,
    Project,
    RectangularSection,
    StructuralModel,
    validate_structural_model,
)
from struct_link import S2kParser, read_project, write_model


def test_local_axes_sap2000_import():
    """Verify that importing SAP2000 model maps Angle=90 on vertical columns to roll_angle=0 in struct_core."""
    ref_file = (
        Path(__file__).resolve().parent.parent
        / "references"
        / "sap2000"
        / "frame_ELF_RSA_Timoshenko.s2k"
    )
    assert ref_file.exists(), f"Reference file not found: {ref_file}"

    proj = read_project(ref_file)
    model = proj.model

    # Check that columns 1 to 12 have roll_angle = 0.0
    for el in model.elements:
        eid = int(el.id)
        if eid <= 12:
            # Columns had Angle=90 in SAP2000, which corresponds to roll_angle=0 in fem3d/struct_core
            assert pytest.approx(el.roll_angle, abs=1e-5) == 0.0
        else:
            # Beams had default Angle=0 in SAP2000
            assert pytest.approx(el.roll_angle, abs=1e-5) == 0.0


def test_local_axes_export_and_roundtrip(tmp_path: Path):
    """
    Verify export and roundtrip preservation of member local axes for:
    - Vertical columns pointing +Z and -Z
    - Horizontal beams
    - Rotated members with arbitrary roll angles
    """
    model = StructuralModel(units="kN, m")

    # Nodes
    # 1: base (0, 0, 0)
    # 2: top (0, 0, 3)
    # 3: cantilever tip (4, 0, 3)
    # 4: skew tip (4, 5, 6)
    # 5: bottom hang (0, 0, -3)
    model.nodes = [
        Node(id=1, x=0.0, y=0.0, z=0.0),
        Node(id=2, x=0.0, y=0.0, z=3.0),
        Node(id=3, x=4.0, y=0.0, z=3.0),
        Node(id=4, x=4.0, y=5.0, z=6.0),
        Node(id=5, x=0.0, y=0.0, z=-3.0),
    ]

    mat = ElasticMaterial(id="CONC", E=3.0e7, nu=0.2, unit_weight=24.0)
    sec = RectangularSection(id="R300x400", b=0.3, h=0.4)
    model.materials.append(mat)
    model.sections.append(sec)

    model.elements = [
        # El 1: Vertical +Z, default roll_angle = 0.0 -> SAP2000 Angle = 90
        BeamElement(id=1, start_node=1, end_node=2, material_id="CONC", section_id="R300x400", roll_angle=0.0),
        # El 2: Vertical +Z, rotated roll_angle = 45.0 -> SAP2000 Angle = 135
        BeamElement(id=2, start_node=1, end_node=2, material_id="CONC", section_id="R300x400", roll_angle=45.0),
        # El 3: Vertical -Z, default roll_angle = 0.0 -> SAP2000 Angle = 90
        BeamElement(id=3, start_node=1, end_node=5, material_id="CONC", section_id="R300x400", roll_angle=0.0),
        # El 4: Horizontal beam +X, default roll_angle = 0.0 -> SAP2000 Angle = 0
        BeamElement(id=4, start_node=2, end_node=3, material_id="CONC", section_id="R300x400", roll_angle=0.0),
        # El 5: Horizontal beam +X, rotated roll_angle = 30.0 -> SAP2000 Angle = 30
        BeamElement(id=5, start_node=2, end_node=3, material_id="CONC", section_id="R300x400", roll_angle=30.0),
        # El 6: Inclined 3D member, rotated roll_angle = -45.0 -> SAP2000 Angle = -45
        BeamElement(id=6, start_node=3, end_node=4, material_id="CONC", section_id="R300x400", roll_angle=-45.0),
    ]

    project = Project(model=model, units="kN, m")
    validate_structural_model(project.model)

    # 1. Export to SAP2000 .s2k
    out_file = tmp_path / "test_axes.s2k"
    write_model(project, out_file)
    assert out_file.exists()

    # 2. Inspect raw S2K table
    s2k_tables = S2kParser.parse_file(out_file)
    assert "FRAME LOCAL AXES ASSIGNMENTS 1 - TYPICAL" in s2k_tables
    axes_rows = {str(r["Frame"]): float(r["Angle"]) for r in s2k_tables["FRAME LOCAL AXES ASSIGNMENTS 1 - TYPICAL"].rows}

    # Verify expected SAP2000 angles:
    # El 1: 0 + 90 = 90
    assert pytest.approx(axes_rows["1"], abs=1e-5) == 90.0
    # El 2: 45 + 90 = 135
    assert pytest.approx(axes_rows["2"], abs=1e-5) == 135.0
    # El 3: 0 + 90 = 90
    assert pytest.approx(axes_rows["3"], abs=1e-5) == 90.0
    # El 4: 0 -> not present in table (defaults to 0)
    assert "4" not in axes_rows
    # El 5: 30
    assert pytest.approx(axes_rows["5"], abs=1e-5) == 30.0
    # El 6: -45
    assert pytest.approx(axes_rows["6"], abs=1e-5) == -45.0

    # 3. Re-import from S2K
    re_proj = read_project(out_file)
    validate_structural_model(re_proj.model)
    re_elements = {int(el.id): el for el in re_proj.model.elements}

    # Verify roundtrip preservation of roll_angle in struct_core
    assert pytest.approx(re_elements[1].roll_angle, abs=1e-5) == 0.0
    assert pytest.approx(re_elements[2].roll_angle, abs=1e-5) == 45.0
    assert pytest.approx(re_elements[3].roll_angle, abs=1e-5) == 0.0
    assert pytest.approx(re_elements[4].roll_angle, abs=1e-5) == 0.0
    assert pytest.approx(re_elements[5].roll_angle, abs=1e-5) == 30.0
    assert pytest.approx(re_elements[6].roll_angle, abs=1e-5) == -45.0

    # 4. Verify that fem3d transformation matrices match 100% between original and re-imported
    st_orig = model_from_core(project)
    st_re = model_from_core(re_proj)

    for eid in (1, 2, 3, 4, 5, 6):
        el_orig = st_orig.elements.get(eid) or st_orig.elements.get(str(eid))
        el_re = st_re.elements.get(eid) or st_re.elements.get(str(eid))
        R_orig = el_orig.rotation_matrix_3x3()
        R_re = el_re.rotation_matrix_3x3()
        np.testing.assert_allclose(R_orig, R_re, atol=1e-6, err_msg=f"Rotation matrix mismatch for Element {eid}")
