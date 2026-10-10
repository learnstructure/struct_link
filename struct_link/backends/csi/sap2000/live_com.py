"""
SAP2000 live COM / OAPI automation session.

Provides interactive model sync and execution with a live running SAP2000 instance.
Requires Windows and a valid CSI SAP2000 installation.
"""

from __future__ import annotations

import sys
from typing import Any, Optional

from struct_core import AnalysisResult, StructuralModel

from ....base import BaseLiveSession


class Sap2000LiveSession(BaseLiveSession):
    """
    Live OAPI / COM session with CSI SAP2000.
    """

    PROG_ID = "CSI.SAP2000.API.SapObject"

    def __init__(self):
        self._sap_object: Any = None
        self._sap_model: Any = None

    def is_connected(self) -> bool:
        return self._sap_model is not None

    def connect(self, attach_existing: bool = True) -> bool:
        """
        Connect to a running SAP2000 instance or launch a new one.

        Parameters
        ----------
        attach_existing : bool
            If True, attempts to bind to the currently open SAP2000 process.
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
                self._sap_object = comtypes.client.GetActiveObject(self.PROG_ID)
                self._sap_model = self._sap_object.SapModel
                return True
            except Exception:
                # If cannot attach to existing, fall through to creation if desired
                pass

        try:
            helper = comtypes.client.CreateObject("cHelper.Helper")
            self._sap_object = helper.CreateObjectProgID(self.PROG_ID)
            self._sap_object.ApplicationStart()
            self._sap_model = self._sap_object.SapModel
            return True
        except Exception as exc:
            raise RuntimeError(
                f"Could not connect to SAP2000: {exc}. Ensure SAP2000 is installed and licensed."
            ) from exc

    def get_active_model(self) -> StructuralModel:
        """
        Extract the model currently active in the SAP2000 window.
        Uses temporary .s2k file export via OAPI for high fidelity.
        """
        if not self.is_connected():
            raise RuntimeError("No active SAP2000 session. Call connect() first.")

        import tempfile
        from pathlib import Path
        from .file_reader import Sap2000FileReader

        with tempfile.NamedTemporaryFile(suffix=".s2k", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            # SAP2000 OAPI command to export text file
            ret = self._sap_model.File.ExportS2K(str(tmp_path), 0)
            if ret != 0:
                raise RuntimeError(f"SAP2000 ExportS2K failed with code {ret}")
            reader = Sap2000FileReader()
            return reader.read_model(tmp_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    def update_model(self, model: StructuralModel) -> None:
        """
        Update the live SAP2000 model with the provided StructuralModel.
        """
        if not self.is_connected():
            raise RuntimeError("No active SAP2000 session. Call connect() first.")

        import tempfile
        from pathlib import Path
        from .file_writer import Sap2000FileWriter

        with tempfile.NamedTemporaryFile(suffix=".s2k", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            writer = Sap2000FileWriter()
            writer.write_model(model, tmp_path)
            # Import text file into active SAP2000 model
            ret = self._sap_model.File.OpenS2K(str(tmp_path))
            if ret != 0:
                raise RuntimeError(f"SAP2000 OpenS2K failed with code {ret}")
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    def run_analysis(self, case_name: Optional[str] = None) -> AnalysisResult:
        """
        Trigger analysis in SAP2000 and return results as struct_core.AnalysisResult.
        """
        if not self.is_connected():
            raise RuntimeError("No active SAP2000 session. Call connect() first.")

        # Run analysis via OAPI
        ret = self._sap_model.Analyze.RunAnalysis()
        if ret != 0:
            raise RuntimeError(f"SAP2000 RunAnalysis failed with code {ret}")

        return self.get_analysis_results(case_name=case_name or "DEAD")

    def get_analysis_results(self, case_name: str = "DEAD") -> AnalysisResult:
        """
        Extract joint displacements and reactions from the solved SAP2000 model into AnalysisResult.
        """
        if not self.is_connected():
            raise RuntimeError("No active SAP2000 session. Call connect() first.")

        from struct_core.analysis_result import (
            AnalysisResult,
            NodeDisplacement,
            NodeReaction,
            NodeResult,
        )

        node_results = {}
        try:
            self._sap_model.Results.Setup.DeselectAllCasesAndCombosForOutput()
            self._sap_model.Results.Setup.SetCaseSelectedForOutput(case_name)

            # 1. Joint Displacements
            res = self._sap_model.Results.JointDispl("ALL", 0)
            if res and len(res) >= 14 and res[0] == 0:
                n_res = res[2]
                objs = res[3]
                u1s, u2s, u3s = res[8], res[9], res[10]
                r1s, r2s, r3s = res[11], res[12], res[13]
                for i in range(n_res):
                    nid = str(objs[i])
                    if nid not in node_results:
                        node_results[nid] = NodeResult(node_id=nid)
                    node_results[nid].displacement = NodeDisplacement(
                        ux=float(u1s[i]),
                        uy=float(u2s[i]),
                        uz=float(u3s[i]),
                        rx=float(r1s[i]),
                        ry=float(r2s[i]),
                        rz=float(r3s[i]),
                    )

            # 2. Joint Reactions
            res_r = self._sap_model.Results.JointReact("ALL", 0)
            if res_r and len(res_r) >= 14 and res_r[0] == 0:
                n_res = res_r[2]
                objs = res_r[3]
                f1s, f2s, f3s = res_r[8], res_r[9], res_r[10]
                m1s, m2s, m3s = res_r[11], res_r[12], res_r[13]
                for i in range(n_res):
                    nid = str(objs[i])
                    if nid not in node_results:
                        node_results[nid] = NodeResult(node_id=nid)
                    node_results[nid].reaction = NodeReaction(
                        fx=float(f1s[i]),
                        fy=float(f2s[i]),
                        fz=float(f3s[i]),
                        mx=float(m1s[i]),
                        my=float(m2s[i]),
                        mz=float(m3s[i]),
                    )
        except Exception:
            pass

        return AnalysisResult(
            analysis_case_id=case_name,
            node_results=node_results,
        )
