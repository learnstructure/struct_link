"""
ETABS .e2k file writer.

Exports a struct_core.StructuralModel or Project into an ETABS text file (.e2k).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Union

from struct_core import Project, StructuralModel

from ....base import BaseModelWriter
from ..s2k_parser import S2kWriter


class EtabsFileWriter(BaseModelWriter):
    """Exports struct_core.StructuralModel or Project into ETABS .e2k text file."""

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

        # 1. Program Info
        tables["PROGRAM INFORMATION"] = [
            {"ProgramName": "ETABS", "Version": "21.0.0", "ProgLevel": "Ultimate"}
        ]

        # 2. Point Coordinates
        coord_rows = []
        for n in model.nodes:
            coord_rows.append(
                {
                    "Point": str(n.id),
                    "X": float(n.x),
                    "Y": float(n.y),
                    "Z": float(getattr(n, "z", 0.0)),
                }
            )
        tables["POINT COORDINATES"] = coord_rows

        # 3. Restraints
        restr_rows = []
        for s in model.supports:
            restr_rows.append(
                {
                    "Point": str(s.node_id),
                    "UX": bool(getattr(s, "ux", False)),
                    "UY": bool(getattr(s, "uy", False)),
                    "UZ": bool(getattr(s, "uz", False)),
                    "RX": bool(getattr(s, "rx", False)),
                    "RY": bool(getattr(s, "ry", False)),
                    "RZ": bool(getattr(s, "rz", False)),
                }
            )
        if restr_rows:
            tables["POINT ASSIGNMENTS - RESTRAINTS"] = restr_rows

        # 4. Materials
        mat_rows = []
        for m in model.materials:
            mat_rows.append(
                {
                    "Material": str(m.id),
                    "Type": "Concrete",
                    "E": float(getattr(m, "E", 2.9e7)),
                    "Nu": float(getattr(m, "nu", 0.3)),
                    "UnitMass": float(getattr(m, "rho", 0.0)),
                }
            )
        if mat_rows:
            tables["MATERIAL PROPERTIES - GENERAL"] = mat_rows

        # 5. Frame Section Properties
        sec_rows = []
        for sec in model.sections:
            sec_type = getattr(sec, "type", "general")
            if sec_type == "rectangular":
                sec_rows.append(
                    {
                        "SectionName": str(sec.id),
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
                        "Shape": "General",
                        "Area": float(getattr(sec, "A", 1.0)),
                        "I33": float(getattr(sec, "Iz", 1.0)),
                        "I22": float(getattr(sec, "Iy", 1.0)),
                        "J": float(getattr(sec, "J", 1.0)),
                    }
                )
        if sec_rows:
            tables["FRAME SECTION PROPERTIES"] = sec_rows

        # 6. Line Connectivity
        conn_rows = []
        default_sec = model.sections[0].id if model.sections else "DEFAULT_SEC"
        for el in model.elements:
            conn_rows.append(
                {
                    "Line": str(el.id),
                    "PointI": str(el.start_node),
                    "PointJ": str(el.end_node),
                    "SectionName": str(getattr(el, "section_id", default_sec)),
                }
            )
        tables["LINE CONNECTIVITY"] = conn_rows

        # 7. Point Loads
        load_rows = []
        for lc in getattr(model, "load_cases", []):
            pat = str(lc.id)
            for pl in getattr(lc, "point_loads", []):
                load_rows.append(
                    {
                        "Point": str(pl.node_id),
                        "LoadPat": pat,
                        "FX": float(getattr(pl, "fx", 0.0)),
                        "FY": float(getattr(pl, "fy", 0.0)),
                        "FZ": float(getattr(pl, "fz", 0.0)),
                        "MX": float(getattr(pl, "mx", 0.0)),
                        "MY": float(getattr(pl, "my", 0.0)),
                        "MZ": float(getattr(pl, "mz", 0.0)),
                    }
                )
        if load_rows:
            tables["POINT LOADS - FORCE"] = load_rows

        return S2kWriter.write_tables(tables, target_path)
