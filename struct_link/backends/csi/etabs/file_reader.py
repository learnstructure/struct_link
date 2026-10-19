"""
ETABS .e2k file reader.

Parses ETABS text files (.e2k / .$et) into struct_core.StructuralModel or Project instances.
"""

from __future__ import annotations

from ..base_reader import CsiFileReader


class EtabsFileReader(CsiFileReader):
    """Imports an ETABS .e2k text file into a struct_core.StructuralModel or Project."""
    pass
