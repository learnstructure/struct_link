"""
ETABS live COM / OAPI automation session.

Provides interactive model sync and execution with a live running ETABS instance.
Requires Windows and a valid CSI ETABS installation.
"""

from __future__ import annotations

import sys
from typing import Any, Optional

from struct_core import AnalysisResult, StructuralModel

from ....base import BaseLiveSession


class EtabsLiveSession(BaseLiveSession):
    """
    Live OAPI / COM session with CSI ETABS.
    """

    PROG_ID = "CSI.ETABS.API.ETABSObject"

    def __init__(self):
        self._etabs_object: Any = None
        self._sap_model: Any = None

    def is_connected(self) -> bool:
        return self._sap_model is not None

    def connect(self, attach_existing: bool = True) -> bool:
        """
        Connect to a running ETABS instance or launch a new one.
        """
        if sys.platform != "win32":
            raise RuntimeError("Live COM automation is only supported on Windows operating systems.")

        try:
            import comtypes.client
        except ImportError as err:
            raise ImportError(
                "The 'comtypes' package is required for live COM automation. "
                "Install it via: pip install comtypes"
            ) from err

        if attach_existing:
            try:
                self._etabs_object = comtypes.client.GetActiveObject(self.PROG_ID)
                self._sap_model = self._etabs_object.SapModel
                return True
            except Exception:
                pass

        try:
            helper = comtypes.client.CreateObject("cHelper.Helper")
            self._etabs_object = helper.CreateObjectProgID(self.PROG_ID)
            self._etabs_object.ApplicationStart()
            self._sap_model = self._etabs_object.SapModel
            return True
        except Exception as exc:
            raise RuntimeError(
                f"Could not connect to ETABS: {exc}. Ensure ETABS is installed and licensed."
            ) from exc

    def get_active_model(self) -> StructuralModel:
        """
        Extract the model currently active in the ETABS window.
        Uses temporary .e2k text file export.
        """
        if not self.is_connected():
            raise RuntimeError("No active ETABS session. Call connect() first.")

        import tempfile
        from pathlib import Path
        from .file_reader import EtabsFileReader

        with tempfile.NamedTemporaryFile(suffix=".e2k", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            ret = self._sap_model.File.ExportE2K(str(tmp_path), 0)
            if ret != 0:
                raise RuntimeError(f"ETABS ExportE2K failed with code {ret}")
            reader = EtabsFileReader()
            return reader.read_model(tmp_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    def update_model(self, model: StructuralModel) -> None:
        """
        Update the live ETABS model with the provided StructuralModel.
        """
        if not self.is_connected():
            raise RuntimeError("No active ETABS session. Call connect() first.")

        import tempfile
        from pathlib import Path
        from .file_writer import EtabsFileWriter

        with tempfile.NamedTemporaryFile(suffix=".e2k", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            writer = EtabsFileWriter()
            writer.write_model(model, tmp_path)
            ret = self._sap_model.File.OpenE2K(str(tmp_path))
            if ret != 0:
                raise RuntimeError(f"ETABS OpenE2K failed with code {ret}")
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    def run_analysis(self) -> AnalysisResult:
        """
        Trigger analysis in ETABS and return results.
        """
        if not self.is_connected():
            raise RuntimeError("No active ETABS session. Call connect() first.")

        ret = self._sap_model.Analyze.RunAnalysis()
        if ret != 0:
            raise RuntimeError(f"ETABS RunAnalysis failed with code {ret}")

        return AnalysisResult(analysis_case_id="ETABS_LIVE")
