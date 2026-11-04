"""
Tests for F1 and F6: support spring rejection on export and import, unhandled table reporting,
and missing units enforcement in struct_link.
"""

from pathlib import Path
import pytest
from struct_core import (
    ElasticMaterial,
    Node,
    Project,
    RectangularSection,
    StructuralModel,
    Support,
)
from struct_link import (
    EtabsFileReader,
    EtabsFileWriter,
    Sap2000FileReader,
    Sap2000FileWriter,
    read_project,
    write_model,
)


def _minimal_model(with_springs: bool = False) -> StructuralModel:
    nodes = [Node(id="1", x=0, y=0, z=0), Node(id="2", x=0, y=0, z=3)]
    mat = ElasticMaterial(id="E1", E=3e7, nu=0.2)
    sec = RectangularSection(id="S1", material_id="E1", b=0.3, h=0.3)
    supports = [
        Support(
            node_id="1",
            ux=True,
            uy=True,
            uz=True,
            rx=True,
            ry=True,
            rz=True,
            ky=1000.0 if with_springs else 0.0,
        )
    ]
    return StructuralModel(
        units="kN, m",
        nodes=nodes,
        materials=[mat],
        sections=[sec],
        supports=supports,
    )


def test_export_model_with_springs_raises_unless_partial_export(tmp_path):
    """F1: Exporting a model with non-zero support springs raises ValueError unless allow_partial_export=True."""
    model = _minimal_model(with_springs=True)
    proj = Project(model=model, units="kN, m")

    s2k_path = tmp_path / "spring_test.s2k"
    e2k_path = tmp_path / "spring_test.e2k"

    # SAP2000 export raises
    with pytest.raises(ValueError, match="not supported for export"):
        Sap2000FileWriter().write_model(proj, s2k_path, allow_partial_export=False)

    # ETABS export raises
    with pytest.raises(ValueError, match="not supported for export"):
        EtabsFileWriter().write_model(proj, e2k_path, allow_partial_export=False)

    # When allow_partial_export=True, both write and record dropped_springs
    sap_writer = Sap2000FileWriter()
    out_s2k = sap_writer.write_model(proj, s2k_path, allow_partial_export=True)
    assert out_s2k.exists()
    assert "1" in sap_writer.dropped_springs

    etabs_writer = EtabsFileWriter()
    out_e2k = etabs_writer.write_model(proj, e2k_path, allow_partial_export=True)
    assert out_e2k.exists()
    assert "1" in etabs_writer.dropped_springs


def test_import_file_with_spring_table_raises_unless_partial_import(tmp_path):
    """F1 & F6: Importing a CSI file with spring assignments raises unless allow_partial_import=True."""
    s2k_text = (
        'TABLE:  "PROGRAM CONTROL"\n'
        '   ProgramName=SAP2000   Version=24.0.0   CurrUnits="KN, m, C"\n'
        'TABLE:  "JOINT COORDINATES"\n'
        '   Joint=1   CoordSys=GLOBAL   X=0   Y=0   Z=0\n'
        'TABLE:  "JOINT RESTRAINTS"\n'
        '   Joint=1   U1=Yes   U2=Yes   U3=Yes\n'
        'TABLE:  "JOINT SPRING ASSIGNMENTS 1 - UNCOUPLED"\n'
        '   Joint=1   CoordSys=GLOBAL   U1=1000   U2=0   U3=0\n'
        'END TABLE DATA\n'
    )
    s2k_file = tmp_path / "with_spring.s2k"
    s2k_file.write_text(s2k_text, encoding="utf-8")

    reader = Sap2000FileReader()

    # Default import raises
    with pytest.raises(ValueError, match="unsupported table"):
        reader.read_model(s2k_file, allow_partial_import=False)

    # allow_partial_import=True imports and notes table
    m = reader.read_model(s2k_file, allow_partial_import=True)
    assert len(m.nodes) == 1
    assert any("JOINT SPRING ASSIGNMENTS" in msg for msg in reader.messages)


def test_file_missing_units_table_raises(tmp_path):
    """F6: Missing units (PROGRAM CONTROL / CurrUnits or $ CONTROLS / UNITS) raises ValueError."""
    # S2K missing CurrUnits
    no_units_s2k = (
        'TABLE:  "JOINT COORDINATES"\n'
        '   Joint=1   CoordSys=GLOBAL   X=0   Y=0   Z=0\n'
        'END TABLE DATA\n'
    )
    f1 = tmp_path / "no_units.s2k"
    f1.write_text(no_units_s2k, encoding="utf-8")

    reader = Sap2000FileReader()
    with pytest.raises(ValueError, match="The file states no units"):
        reader.read_model(f1)

    # E2K dollar format missing CONTROLS / UNITS
    no_units_e2k = (
        '$ POINT COORDINATES\n'
        '  POINT "1" 0.0 0.0\n'
        '$ END OF MODEL FILE\n'
    )
    f2 = tmp_path / "no_units.e2k"
    f2.write_text(no_units_e2k, encoding="utf-8")

    reader_e = EtabsFileReader()
    with pytest.raises(ValueError, match="The file states no units"):
        reader_e.read_model(f2)


def test_unhandled_tables_recorded_in_messages(tmp_path):
    """F6: Unhandled tables are not silently dropped; they are logged in reader.messages."""
    s2k_text = (
        'TABLE:  "PROGRAM CONTROL"\n'
        '   ProgramName=SAP2000   Version=24.0.0   CurrUnits="KN, m, C"\n'
        'TABLE:  "JOINT COORDINATES"\n'
        '   Joint=1   CoordSys=GLOBAL   X=0   Y=0   Z=0\n'
        'TABLE:  "SOME CUSTOM UNHANDLED CSI TABLE"\n'
        '   Col1=Val1   Col2=Val2\n'
        '   Col1=Val3   Col2=Val4\n'
        'END TABLE DATA\n'
    )
    f = tmp_path / "unhandled.s2k"
    f.write_text(s2k_text, encoding="utf-8")

    reader = Sap2000FileReader()
    m = reader.read_model(f)
    assert len(m.nodes) == 1
    assert any("SOME CUSTOM UNHANDLED CSI TABLE" in msg and "2 rows" in msg for msg in reader.messages)


def test_ignored_tables_not_in_messages(tmp_path):
    """H3: Ignorable tables go to ignored_tables, not to reader.messages."""
    s2k_text = (
        'TABLE:  "PROGRAM CONTROL"\n'
        '   ProgramName=SAP2000   Version=24.0.0   CurrUnits="KN, m, C"\n'
        'TABLE:  "JOINT COORDINATES"\n'
        '   Joint=1   CoordSys=GLOBAL   X=0   Y=0   Z=0\n'
        'TABLE:  "OPTIONS - COLORS - DISPLAY"\n'
        '   ColorType=Screen   Color=Black\n'
        'TABLE:  "GRID LINES"\n'
        '   CoordSys=GLOBAL   AxisDir=X   GridId=1   XCoord=0\n'
        'END TABLE DATA\n'
    )
    f = tmp_path / "cosmetic.s2k"
    f.write_text(s2k_text, encoding="utf-8")

    reader = Sap2000FileReader()
    m = reader.read_model(f)
    assert len(m.nodes) == 1
    assert "OPTIONS - COLORS - DISPLAY" in reader.ignored_tables
    assert "GRID LINES" in reader.ignored_tables
    assert not any("OPTIONS - COLORS - DISPLAY" in msg for msg in reader.messages)
    assert not any("GRID LINES" in msg for msg in reader.messages)


def test_property_modifier_and_joint_mass_import_and_cantilever(tmp_path):
    """H3: FRAME PROPERTY MODIFIERS and JOINT ADDED MASS are imported and affect mechanics."""
    from fem2d import model_from_core, Results

    s2k_text = (
        'TABLE:  "PROGRAM CONTROL"\n'
        '   ProgramName=SAP2000   Version=24.0.0   CurrUnits="KN, m, C"\n'
        'TABLE:  "MATERIAL PROPERTIES 01 - GENERAL"\n'
        '   Material="CONC"   Type="Concrete"\n'
        'TABLE:  "MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES"\n'
        '   Material="CONC"   UnitMass=2.4   UnitWeight=23.5   E1=30000000   U12=0.2\n'
        'TABLE:  "FRAME SECTION PROPERTIES 01 - GENERAL"\n'
        '   SectionName="BEAM"   Material="CONC"   Shape="Rectangular"   t3=0.5   t2=0.3\n'
        'TABLE:  "JOINT COORDINATES"\n'
        '   Joint=1   CoordSys=GLOBAL   X=0   Y=0   Z=0\n'
        '   Joint=2   CoordSys=GLOBAL   X=5   Y=0   Z=0\n'
        'TABLE:  "JOINT RESTRAINTS"\n'
        '   Joint=1   U1=Yes   U2=Yes   U3=Yes   R1=Yes   R2=Yes   R3=Yes\n'
        'TABLE:  "JOINT ADDED MASS ASSIGNMENTS"\n'
        '   Joint=2   Mass1=2.5   Mass2=2.5   Mass3=2.5\n'
        'TABLE:  "CONNECTIVITY - FRAME"\n'
        '   Frame=1   JointI=1   JointJ=2\n'
        'TABLE:  "FRAME SECTION ASSIGNMENTS"\n'
        '   Frame=1   SectionName="BEAM"\n'
        'TABLE:  "FRAME PROPERTY MODIFIERS"\n'
        '   Frame=1   I3Mod=0.35   I2Mod=0.35\n'
        'TABLE:  "LOAD PATTERN DEFINITIONS"\n'
        '   LoadPat="LIVE"   DesignType="Live"   SelfWtMult=0\n'
        'TABLE:  "JOINT LOADS - FORCE"\n'
        '   Joint=2   LoadPat="LIVE"   F2=-10.0\n'
        'TABLE:  "COMBINATION DEFINITIONS"\n'
        '   ComboName="COMB1"   ComboType="Linear Add"   LoadName="LIVE"   SF=1.2\n'
        'END TABLE DATA\n'
    )
    f = tmp_path / "modified_cantilever.s2k"
    f.write_text(s2k_text, encoding="utf-8")

    reader = Sap2000FileReader()
    proj = reader.read_project(f)
    m = proj.model

    # Check imported modifiers, mass, combinations
    elem = m.elements[0]
    assert elem.stiffness_modifiers is not None
    assert pytest.approx(elem.stiffness_modifiers["I3"]) == 0.35
    assert pytest.approx(elem.stiffness_modifiers["I2"]) == 0.35

    n2 = next(n for n in m.nodes if n.id == "2")
    assert pytest.approx(n2.mass) == 2.5

    assert len(m.load_combinations) == 1
    combo = m.load_combinations[0]
    assert combo.name == "COMB1"
    assert any(f.load_case_id == "LIVE" and f.factor == 1.2 for f in combo.factors)

    # Check mechanics: I3 = 0.35 gives 1/0.35 times the deflection of unmodified beam
    s_mod = model_from_core(proj, load_case_id="LIVE", include_shear_deformation=False)
    s_mod.solve()
    d_mod = abs(Results(s_mod).node_displacements()["uy"].iloc[1])

    # Unmodified version
    elem_unmod = elem.model_copy(update={"stiffness_modifiers": None})
    m_unmod = m.model_copy(update={"elements": [elem_unmod]})
    s_unmod = model_from_core(proj.model_copy(update={"model": m_unmod}), load_case_id="LIVE", include_shear_deformation=False)
    s_unmod.solve()
    d_unmod = abs(Results(s_unmod).node_displacements()["uy"].iloc[1])

    assert pytest.approx(d_mod / d_unmod, rel=1e-5) == 1.0 / 0.35

