"""
Unit and roundtrip tests for struct_link CSI (SAP2000 & ETABS) serializers and readers.
"""

import tempfile
from pathlib import Path
import pytest
from struct_core import (
    StructuralModel,
    Node,
    BeamElement,
    ElasticMaterial,
    RectangularSection,
    CircularSection,
    Support,
    PointLoad,
    DistributedLoad,
    LoadCase,
)
from struct_link import (
    read_model,
    write_model,
    Sap2000FileReader,
    Sap2000FileWriter,
    EtabsFileReader,
    EtabsFileWriter,
    get_reader,
    get_writer,
    get_live_session,
)


def _build_test_model():
    nodes = [
        Node(id="1", x=0.0, y=0.0, z=0.0),
        Node(id="2", x=6.0, y=0.0, z=0.0),
        Node(id="3", x=0.0, y=0.0, z=3.5),
        Node(id="4", x=6.0, y=0.0, z=3.5),
    ]

    supports = [
        Support(node_id="1", ux=True, uy=True, uz=True, rx=True, ry=True, rz=True),
        Support(node_id="2", ux=True, uy=True, uz=True, rx=True, ry=True, rz=True),
    ]

    materials = [
        ElasticMaterial(id="C30", E=3.0e7, nu=0.2, rho=2500.0),
    ]

    sections = [
        RectangularSection(id="COL", material_id="C30", b=0.4, h=0.4),
        RectangularSection(id="BEAM", material_id="C30", b=0.3, h=0.6),
    ]

    elements = [
        BeamElement(id="C1", start_node="1", end_node="3", section_id="COL"),
        BeamElement(id="C2", start_node="2", end_node="4", section_id="COL"),
        BeamElement(id="B1", start_node="3", end_node="4", section_id="BEAM"),
    ]

    load_cases = [
        LoadCase(
            id="DEAD",
            point_loads=[
                PointLoad(node_id="3", fz=-15.0),
                PointLoad(node_id="4", fz=-15.0),
            ],
            element_loads=[
                DistributedLoad(element_id="B1", wy=-25.0),
            ],
        )
    ]

    return StructuralModel(
        units="kN, m",
        nodes=nodes,
        elements=elements,
        materials=materials,
        sections=sections,
        supports=supports,
        load_cases=load_cases,
    )


def test_sap2000_roundtrip(tmp_path):
    orig_model = _build_test_model()
    s2k_file = tmp_path / "frame.s2k"

    # Export via writer
    writer = Sap2000FileWriter()
    writer.write_model(orig_model, s2k_file)
    assert s2k_file.exists()

    # Import via reader
    reader = Sap2000FileReader()
    imported = reader.read_model(s2k_file)

    # Verify nodes
    assert len(imported.nodes) == 4
    n_map = {n.id: n for n in imported.nodes}
    assert n_map["1"].x == 0.0
    assert n_map["2"].x == 6.0
    assert n_map["3"].z == 3.5

    # Verify supports
    assert len(imported.supports) == 2
    s_map = {s.node_id: s for s in imported.supports}
    assert s_map["1"].ux is True
    assert s_map["1"].uz is True
    assert s_map["1"].rz is True

    # Verify elements
    assert len(imported.elements) == 3
    e_map = {e.id: e for e in imported.elements}
    assert e_map["C1"].start_node == "1"
    assert e_map["C1"].end_node == "3"
    assert e_map["B1"].start_node == "3"
    assert e_map["B1"].end_node == "4"

    # Verify loads
    assert len(imported.load_cases) >= 1
    dead_case = imported.load_cases[0]
    assert len(dead_case.point_loads) == 2
    assert len(dead_case.element_loads) == 1


def test_etabs_roundtrip(tmp_path):
    orig_model = _build_test_model()
    e2k_file = tmp_path / "frame.e2k"

    # Export via writer
    writer = EtabsFileWriter()
    writer.write_model(orig_model, e2k_file)
    assert e2k_file.exists()

    # Import via reader
    reader = EtabsFileReader()
    imported = reader.read_model(e2k_file)

    # Verify nodes and elements
    assert len(imported.nodes) == 4
    assert len(imported.elements) == 3
    assert len(imported.supports) == 2


def test_auto_registry_dispatch(tmp_path):
    orig_model = _build_test_model()
    s2k_file = tmp_path / "model.s2k"
    e2k_file = tmp_path / "model.e2k"

    # Write using top-level write_model
    write_model(orig_model, s2k_file)
    write_model(orig_model, e2k_file)

    # Read using top-level read_model
    m1 = read_model(s2k_file)
    m2 = read_model(e2k_file)

    assert len(m1.nodes) == 4
    assert len(m2.nodes) == 4


def test_registry_lookups():
    assert isinstance(get_reader(".s2k"), Sap2000FileReader)
    assert isinstance(get_reader("sap2000"), Sap2000FileReader)
    assert isinstance(get_reader(".e2k"), EtabsFileReader)
    assert isinstance(get_reader("etabs"), EtabsFileReader)

    assert isinstance(get_writer(".s2k"), Sap2000FileWriter)
    assert isinstance(get_writer(".e2k"), EtabsFileWriter)

    with pytest.raises(ValueError):
        get_reader(".unknown_ext")


def test_live_session_lookup():
    session = get_live_session("sap2000")
    assert not session.is_connected()


def test_fixture_files():
    fixtures_dir = Path(__file__).parent / "fixtures"
    s2k_path = fixtures_dir / "sample_frame.s2k"
    e2k_path = fixtures_dir / "sample_frame.e2k"

    m_sap = read_model(s2k_path)
    assert len(m_sap.nodes) == 4
    assert len(m_sap.elements) == 3
    assert len(m_sap.supports) == 2
    assert len(m_sap.load_cases) == 1

    m_etabs = read_model(e2k_path)
    assert len(m_etabs.nodes) == 4
    assert len(m_etabs.elements) == 3
    assert len(m_etabs.supports) == 2






def test_reinforced_concrete_roundtrip_keeps_strengths_bars_and_cover(tmp_path):
    """Typed materials (f'c, fy, fu), section materials, bar materials and column cover survive SAP2000 and ETABS;
    nothing is invented for a material without them."""
    from struct_core import ConcreteMaterial, Project, RebarMaterial
    from struct_link import read_project, write_model
    from struct_link.backends.csi.base_reader import CsiFileReader

    model = StructuralModel(
        nodes=[Node(id="1", x=0, y=0, z=0), Node(id="2", x=5, y=0, z=0), Node(id="3", x=0, y=0, z=3),
               Node(id="4", x=5, y=0, z=3)],
        supports=[Support(node_id="1", ux=True, uy=True, uz=True, rx=True, ry=True, rz=True),
                  Support(node_id="2", ux=True, uy=True, uz=True, rx=True, ry=True, rz=True)],
        materials=[ConcreteMaterial(id="C35", fc=35_000.0, E=2.8e7, unit_weight=24.0),
                   RebarMaterial(id="B500", fy=500_000.0, fu=550_000.0, E=2.0e8, unit_weight=77.0),
                   ElasticMaterial(id="PLAIN", E=3.0e7, unit_weight=24.0)],
        sections=[RectangularSection(id="COL", material_id="C35", b=0.4, h=0.4, rebar_material_id="B500",
                                     transverse_rebar_material_id="B500", cover=0.05),
                  RectangularSection(id="BEAM", material_id="C35", b=0.3, h=0.6, rebar_material_id="B500",
                                     transverse_rebar_material_id="B500", cover=0.04),
                  RectangularSection(id="TIE", material_id="PLAIN", b=0.2, h=0.2)],
        elements=[BeamElement(id="C1", start_node="1", end_node="3", section_id="COL"),
                  BeamElement(id="C2", start_node="2", end_node="4", section_id="COL"),
                  BeamElement(id="B1", start_node="3", end_node="4", section_id="BEAM"),
                  BeamElement(id="T1", start_node="1", end_node="4", section_id="TIE")],
        units="kN, m",
    )
    for suffix in (".s2k", ".e2k"):
        path = tmp_path / f"rc{suffix}"
        write_model(Project(model=model, units="kN, m"), str(path))
        reader = CsiFileReader()
        back = reader.read_project(str(path)).model
        materials = {m.id: m for m in back.materials}
        assert (materials["C35"].type, materials["C35"].fc) == ("concrete", 35_000.0)
        assert (materials["B500"].type, materials["B500"].fy, materials["B500"].fu) == ("rebar", 500_000.0, 550_000.0)
        assert materials["PLAIN"].type == "elastic" and "FC" not in path.read_text().upper().split("PLAIN")[-1][:40]
        sections = {s.id: s for s in back.sections}
        assert sections["COL"].material_id == "C35" and sections["TIE"].material_id == "PLAIN"
        assert (sections["COL"].rebar_material_id, sections["COL"].transverse_rebar_material_id) == ("B500", "B500")
        assert sections["COL"].cover == pytest.approx(0.05)
        assert sections["BEAM"].rebar_material_id == "B500" and sections["BEAM"].cover is None  # centroid cover
        # every element reaches its material through its section (ETABS renumbers elements)
        assert {back.section_of(e).id: back.material_of(e).id for e in back.elements} == {
            "COL": "C35", "BEAM": "C35", "TIE": "PLAIN"}



def test_sap2000_releases_roundtrip(tmp_path):
    orig = _build_test_model()
    beam = next(e for e in orig.elements if e.id == "B1")
    beam.release_i = [False, False, False, False, False, True]
    beam.release_j = [False, False, False, False, False, True]

    s2k_file = tmp_path / "frame_releases.s2k"
    Sap2000FileWriter().write_model(orig, s2k_file)
    assert s2k_file.exists()

    imported = Sap2000FileReader().read_model(s2k_file)
    imp_beam = next(e for e in imported.elements if str(e.id) == "B1")
    assert imp_beam.release_i == [False, False, False, False, False, True]
    assert imp_beam.release_j == [False, False, False, False, False, True]


def test_etabs_releases_roundtrip(tmp_path):
    orig = _build_test_model()
    beam = next(e for e in orig.elements if e.id == "B1")
    beam.release_i = [False, False, False, False, False, True]
    beam.release_j = [False, False, False, False, False, True]

    e2k_file = tmp_path / "frame_releases.e2k"
    EtabsFileWriter().write_model(orig, e2k_file)
    assert e2k_file.exists()

    imported = EtabsFileReader().read_model(e2k_file)
    imp_beam = next(e for e in imported.elements if str(getattr(e, "section_id", "")) == "BEAM")
    assert imp_beam.release_i == [False, False, False, False, False, True]
    assert imp_beam.release_j == [False, False, False, False, False, True]
