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
        RectangularSection(id="COL", b=0.4, h=0.4),
        RectangularSection(id="BEAM", b=0.3, h=0.6),
    ]

    elements = [
        BeamElement(id="C1", start_node="1", end_node="3", material_id="C30", section_id="COL"),
        BeamElement(id="C2", start_node="2", end_node="4", material_id="C30", section_id="COL"),
        BeamElement(id="B1", start_node="3", end_node="4", material_id="C30", section_id="BEAM"),
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




