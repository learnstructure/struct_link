"""
struct_link: Commercial CAE/FEM Interoperability Link for the Structural Engineering Ecosystem.

Bridges SAP2000, ETABS, and other commercial software with struct_core.
"""

from .base import (
    BackendCapabilities,
    BaseLiveSession,
    BaseModelReader,
    BaseModelWriter,
)
from .backends.csi.s2k_parser import S2kParser, S2kTable, S2kWriter
from .backends.csi.sap2000.file_reader import Sap2000FileReader
from .backends.csi.sap2000.file_writer import Sap2000FileWriter
from .backends.csi.sap2000.live_com import Sap2000LiveSession
from .backends.csi.etabs.file_reader import EtabsFileReader
from .backends.csi.etabs.file_writer import EtabsFileWriter
from .backends.csi.etabs.live_com import EtabsLiveSession
from .registry import (
    get_live_session,
    get_reader,
    get_writer,
    read_model,
    read_project,
    write_model,
)

__all__ = [
    "BackendCapabilities",
    "BaseModelReader",
    "BaseModelWriter",
    "BaseLiveSession",
    "S2kParser",
    "S2kTable",
    "S2kWriter",
    "Sap2000FileReader",
    "Sap2000FileWriter",
    "Sap2000LiveSession",
    "EtabsFileReader",
    "EtabsFileWriter",
    "EtabsLiveSession",
    "get_reader",
    "get_writer",
    "get_live_session",
    "read_model",
    "read_project",
    "write_model",
]
