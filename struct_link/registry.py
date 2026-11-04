"""
Registry and auto-dispatch for external CAE/FEM software backends.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Type, Union

from struct_core import Project, StructuralModel

from .base import BaseLiveSession, BaseModelReader, BaseModelWriter
from .backends.csi.sap2000.file_reader import Sap2000FileReader
from .backends.csi.sap2000.file_writer import Sap2000FileWriter
from .backends.csi.sap2000.live_com import Sap2000LiveSession
from .backends.csi.etabs.file_reader import EtabsFileReader
from .backends.csi.etabs.file_writer import EtabsFileWriter
from .backends.csi.etabs.live_com import EtabsLiveSession

_READERS: Dict[str, Type[BaseModelReader]] = {
    "sap2000": Sap2000FileReader,
    "sap": Sap2000FileReader,
    ".s2k": Sap2000FileReader,
    ".$2k": Sap2000FileReader,
    "etabs": EtabsFileReader,
    ".e2k": EtabsFileReader,
    ".$et": EtabsFileReader,
}

_WRITERS: Dict[str, Type[BaseModelWriter]] = {
    "sap2000": Sap2000FileWriter,
    "sap": Sap2000FileWriter,
    ".s2k": Sap2000FileWriter,
    ".$2k": Sap2000FileWriter,
    "etabs": EtabsFileWriter,
    ".e2k": EtabsFileWriter,
    ".$et": EtabsFileWriter,
}

_SESSIONS: Dict[str, Type[BaseLiveSession]] = {
    "sap2000": Sap2000LiveSession,
    "sap": Sap2000LiveSession,
    "etabs": EtabsLiveSession,
}


def get_reader(backend_or_ext: str) -> BaseModelReader:
    """Retrieve an instantiated model reader by backend name or file extension."""
    key = backend_or_ext.lower().strip()
    cls = _READERS.get(key)
    if cls is None:
        raise ValueError(
            f"No reader registered for {backend_or_ext!r}. "
            f"Supported backends/extensions: {list(_READERS.keys())}"
        )
    return cls()


def get_writer(backend_or_ext: str) -> BaseModelWriter:
    """Retrieve an instantiated model writer by backend name or file extension."""
    key = backend_or_ext.lower().strip()
    cls = _WRITERS.get(key)
    if cls is None:
        raise ValueError(
            f"No writer registered for {backend_or_ext!r}. "
            f"Supported backends/extensions: {list(_WRITERS.keys())}"
        )
    return cls()


def get_live_session(software: str) -> BaseLiveSession:
    """Retrieve an instantiated live automation session by software name."""
    key = software.lower().strip()
    cls = _SESSIONS.get(key)
    if cls is None:
        raise ValueError(
            f"No live session registered for {software!r}. "
            f"Supported software: {list(_SESSIONS.keys())}"
        )
    return cls()


def read_model(
    source: Union[str, Path],
    software: Optional[str] = None,
    allow_partial_import: bool = False,
) -> StructuralModel:
    """
    Import a model from a commercial software file.
    Auto-detects software from file extension if not provided.
    """
    path = Path(source)
    backend_key = software if software else path.suffix.lower()
    reader = get_reader(backend_key)
    return reader.read_model(path, allow_partial_import=allow_partial_import)


def read_project(
    source: Union[str, Path],
    software: Optional[str] = None,
    title: Optional[str] = None,
    allow_partial_import: bool = False,
) -> Project:
    """
    Import a Project from a commercial software file.
    Auto-detects software from file extension if not provided.
    """
    path = Path(source)
    backend_key = software if software else path.suffix.lower()
    reader = get_reader(backend_key)
    return reader.read_project(path, title=title, allow_partial_import=allow_partial_import)


def write_model(
    model_or_project: Union[StructuralModel, Project],
    target_path: Union[str, Path],
    software: Optional[str] = None,
    allow_partial_export: bool = False,
) -> Path:
    """
    Export a model to a commercial software file.
    Auto-detects software from target path extension if not provided.
    """
    path = Path(target_path)
    backend_key = software if software else path.suffix.lower()
    writer = get_writer(backend_key)
    return writer.write_model(model_or_project, path, allow_partial_export=allow_partial_export)

