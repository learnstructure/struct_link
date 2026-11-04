"""
CSI files without a mass source definition must import with the CSI default
(element self-mass only), not with no mass source.
"""

from pathlib import Path

import pytest

from struct_link import read_project

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.mark.parametrize("name", ["sample_frame.s2k", "sample_frame.e2k"])
def test_missing_mass_source_defaults_to_element_mass(name):
    path = FIXTURES / name
    assert "MASS SOURCE" not in path.read_text().upper(), "fixture must not define a mass source"

    model = read_project(path).model

    assert model.mass_source is not None
    assert model.mass_source.include_element_mass is True
    assert model.mass_source.factors == []
    assert model.mass_sources == [model.mass_source]
