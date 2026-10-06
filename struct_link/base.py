"""
Base interfaces and data structures for external structural software links.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from struct_core import AnalysisResult, Project, StructuralModel


@dataclass
class BackendCapabilities:
    """Declared capabilities of a software backend."""

    software_name: str
    file_extensions: List[str] = field(default_factory=list)
    supports_file_import: bool = True
    supports_file_export: bool = True
    supports_live_com: bool = False
    supports_results_import: bool = False


class BaseModelReader(ABC):
    """Abstract base class for importing external models into struct_core."""

    @abstractmethod
    def read_model(self, source: Union[str, Path]) -> StructuralModel:
        """Parse source file/data into a StructuralModel."""
        raise NotImplementedError

    def read_project(self, source: Union[str, Path], title: Optional[str] = None) -> Project:
        """Convenience method to parse and wrap in a struct_core.Project."""
        model = self.read_model(source)
        project = Project(model=model)
        if title:
            project.metadata.title = title
        elif hasattr(source, "stem"):
            project.metadata.title = source.stem
        else:
            project.metadata.title = Path(source).stem
        return project


class BaseModelWriter(ABC):
    """Abstract base class for exporting struct_core models to external software."""

    @abstractmethod
    def write_model(
        self,
        model_or_project: Union[StructuralModel, Project],
        target_path: Union[str, Path],
    ) -> Path:
        """Write a StructuralModel or Project to the target file."""
        raise NotImplementedError


class BaseLiveSession(ABC):
    """
    Abstract base class for live runtime automation (COM / OAPI / RPC).
    Enables interacting with a running software instance.
    """

    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if connection to running application instance is alive."""
        raise NotImplementedError

    @abstractmethod
    def connect(self, attach_existing: bool = True) -> bool:
        """Connect to or launch an instance of the target software."""
        raise NotImplementedError

    @abstractmethod
    def get_active_model(self) -> StructuralModel:
        """Extract the model currently open in the active software window."""
        raise NotImplementedError

    @abstractmethod
    def update_model(self, model: StructuralModel) -> None:
        """Push structural changes into the active software model."""
        raise NotImplementedError

    @abstractmethod
    def run_analysis(self) -> AnalysisResult:
        """Trigger analysis in the live software and return results as struct_core.AnalysisResult."""
        raise NotImplementedError
