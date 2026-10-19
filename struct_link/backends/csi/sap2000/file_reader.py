"""
SAP2000 .s2k file reader.

Parses SAP2000 text files (.s2k / .$2k) into struct_core.StructuralModel or Project instances.
"""

from __future__ import annotations

from ..base_reader import CsiFileReader


class Sap2000FileReader(CsiFileReader):
    """Imports a SAP2000 .s2k text file into a struct_core.StructuralModel or Project."""
    pass
