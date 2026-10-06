"""
SAP2000 .s2k file writer.

Exports a struct_core.StructuralModel or Project into a standard SAP2000 text file (.s2k).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Union

from struct_core import Project, StructuralModel

from ....base import BaseModelWriter
from ..s2k_parser import S2kWriter


class Sap2000FileWriter(BaseModelWriter):
    """Exports struct_core.StructuralModel or Project into SAP2000 .s2k text file."""

    def write_model(
        self,
        model_or_project: Union[StructuralModel, Project],
        target_path: Union[str, Path],
    ) -> Path:
        model = (
            model_or_project.model
            if isinstance(model_or_project, Project)
            else model_or_project
        )
        if model is None:
            raise ValueError("Provided project contains no structural model.")

        tables: Dict[str, List[Dict[str, Any]]] = {}

        # 1. Program Information
        tables["PROGRAM INFORMATION"] = [
            {"ProgramName": "SAP2000", "Version": "24.0.0", "ProgLevel": "Ultimate"}
        ]

        # 2. Joint Coordinates
        coord_rows = []
        for n in model.nodes:
            coord_rows.append(
                {
                    "Joint": str(n.id),
                    "CoordSys": "GLOBAL",
                    "CoordType": "Cartesian",
                    "XorR": float(n.x),
                    "Y": float(n.y),
                    "Z": float(getattr(n, "z", 0.0)),
                    "SpecialJt": "No",
                }
            )
        tables["JOINT COORDINATES"] = coord_rows

        # 3. Joint Restraints
        restr_rows = []
        for s in model.supports:
            restr_rows.append(
                {
                    "Joint": str(s.node_id),
                    "U1": bool(getattr(s, "ux", False)),
                    "U2": bool(getattr(s, "uy", False)),
                    "U3": bool(getattr(s, "uz", False)),
                    "R1": bool(getattr(s, "rx", False)),
                    "R2": bool(getattr(s, "ry", False)),
                    "R3": bool(getattr(s, "rz", False)),
                }
            )
        if restr_rows:
            tables["JOINT RESTRAINTS"] = restr_rows

        # 4. Materials
        mat_rows = []
        for m in model.materials:
            mat_rows.append(
                {
                    "Material": str(m.id),
                    "UnitMass": float(getattr(m, "rho", 0.0)),
                    "UnitWeight": float(getattr(m, "rho", 0.0) * 9.81),
                    "E1": float(getattr(m, "E", 2.9e7)),
                    "U12": float(getattr(m, "nu", 0.3)),
                    "A1": 0.0,
                    "G12": float(getattr(m, "E", 2.9e7) / (2.0 * (1.0 + getattr(m, "nu", 0.3)))),
                }
            )
        if mat_rows:
            tables["MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES"] = mat_rows

        # 5. Frame Section Properties
        sec_rows = []
        for sec in model.sections:
            sec_type = getattr(sec, "type", "general")
            mat_id = model.materials[0].id if model.materials else "CONC"
            if sec_type == "rectangular":
                sec_rows.append(
                    {
                        "SectionName": str(sec.id),
                        "Material": mat_id,
                        "Shape": "Rectangular",
                        "t3": float(sec.h),
                        "t2": float(sec.b),
                        "Area": float(sec.b * sec.h),
                    }
                )
            elif sec_type == "circular":
                import math
                sec_rows.append(
                    {
                        "SectionName": str(sec.id),
                        "Material": mat_id,
                        "Shape": "Circular",
                        "t3": float(sec.d),
                        "t2": float(sec.d),
                        "Area": float(math.pi * (sec.d / 2.0) ** 2),
                    }
                )
            else:
                sec_rows.append(
                    {
                        "SectionName": str(sec.id),
                        "Material": mat_id,
                        "Shape": "General",
                        "Area": float(getattr(sec, "A", 1.0)),
                        "I33": float(getattr(sec, "Iz", 1.0)),
                        "I22": float(getattr(sec, "Iy", 1.0)),
                        "J": float(getattr(sec, "J", 1.0)),
                    }
                )
        if sec_rows:
            tables["FRAME SECTION PROPERTIES 01 - GENERAL"] = sec_rows

        # 6. Connectivity - Frame & Section Assignments
        conn_rows = []
        sec_assign_rows = []
        default_sec = model.sections[0].id if model.sections else "DEFAULT_SEC"

        for el in model.elements:
            fid = str(el.id)
            conn_rows.append(
                {
                    "Frame": fid,
                    "JointI": str(el.start_node),
                    "JointJ": str(el.end_node),
                    "IsCurved": "No",
                }
            )
            sec_name = str(getattr(el, "section_id", default_sec))
            sec_assign_rows.append(
                {
                    "Frame": fid,
                    "SectionName": sec_name,
                    "AutoSelect": "N.A.",
                }
            )

        tables["CONNECTIVITY - FRAME"] = conn_rows
        tables["FRAME SECTION ASSIGNMENTS"] = sec_assign_rows

        # 7. Loads
        jt_load_rows = []
        frm_load_rows = []

        for lc in getattr(model, "load_cases", []):
            pat = str(lc.id)
            for pl in getattr(lc, "point_loads", []):
                jt_load_rows.append(
                    {
                        "Joint": str(pl.node_id),
                        "LoadPat": pat,
                        "CoordSys": "GLOBAL",
                        "Type": "Force",
                        "F1": float(getattr(pl, "fx", 0.0)),
                        "F2": float(getattr(pl, "fy", 0.0)),
                        "F3": float(getattr(pl, "fz", 0.0)),
                        "M1": float(getattr(pl, "mx", 0.0)),
                        "M2": float(getattr(pl, "my", 0.0)),
                        "M3": float(getattr(pl, "mz", 0.0)),
                    }
                )

            for dl in getattr(lc, "element_loads", []):
                val = getattr(dl, "wy", 0.0) or getattr(dl, "wz", 0.0)
                dir_str = "Gravity" if getattr(dl, "wy", 0.0) < 0 else "Z"
                frm_load_rows.append(
                    {
                        "Frame": str(dl.element_id),
                        "LoadPat": pat,
                        "CoordSys": "GLOBAL",
                        "Type": "Force",
                        "Dir": dir_str,
                        "DistType": "RelDist",
                        "RelDistA": 0.0,
                        "RelDistB": 1.0,
                        "FValA": float(abs(val)),
                        "FValB": float(abs(val)),
                    }
                )

        if jt_load_rows:
            tables["JOINT LOADS - FORCE"] = jt_load_rows
        if frm_load_rows:
            tables["FRAME LOADS - DISTRIBUTED"] = frm_load_rows

        return S2kWriter.write_tables(tables, target_path)
