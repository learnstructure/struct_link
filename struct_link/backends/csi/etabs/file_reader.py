"""
ETABS .e2k file reader.

Parses ETABS text files (.e2k / .$et) into struct_core.StructuralModel instances.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from struct_core import (
    BeamElement,
    CircularSection,
    DistributedLoad,
    ElasticMaterial,
    GeneralSection,
    LoadCase,
    Node,
    PointLoad,
    RectangularSection,
    StructuralModel,
    Support,
)

from ....base import BaseModelReader
from ..s2k_parser import S2kParser, S2kTable


class EtabsFileReader(BaseModelReader):
    """Imports an ETABS .e2k text file into a struct_core.StructuralModel."""

    def read_model(self, source: Union[str, Path]) -> StructuralModel:
        tables = S2kParser.parse_file(source)
        return self._build_model(tables)

    def _build_model(self, tables: Dict[str, S2kTable]) -> StructuralModel:
        nodes: List[Node] = []
        supports: List[Support] = []
        materials: List[ElasticMaterial] = []
        sections: List[Union[RectangularSection, CircularSection, GeneralSection]] = []
        elements: List[BeamElement] = []
        load_cases_dict: Dict[str, LoadCase] = {}

        # 1. Point Coordinates -> Nodes
        # Tables: "POINT COORDINATES" or "JOINT COORDINATES"
        coord_table = tables.get("POINT COORDINATES") or tables.get("JOINT COORDINATES")
        if coord_table:
            for row in coord_table.rows:
                pid = str(row.get("Point", row.get("Joint", row.get("PointID", ""))))
                if not pid:
                    continue
                x = float(row.get("X", row.get("XorR", 0.0)))
                y = float(row.get("Y", 0.0))
                z = float(row.get("Z", 0.0))
                nodes.append(Node(id=pid, x=x, y=y, z=z))

        # 2. Restraints -> Supports
        # Tables: "POINT ASSIGNMENTS - RESTRAINTS" or "JOINT RESTRAINTS"
        restr_table = tables.get("POINT ASSIGNMENTS - RESTRAINTS") or tables.get("JOINT RESTRAINTS")
        if restr_table:
            for row in restr_table.rows:
                pid = str(row.get("Point", row.get("Joint", "")))
                if not pid:
                    continue
                supports.append(
                    Support(
                        node_id=pid,
                        ux=bool(row.get("UX", row.get("U1", False))),
                        uy=bool(row.get("UY", row.get("U2", False))),
                        uz=bool(row.get("UZ", row.get("U3", False))),
                        rx=bool(row.get("RX", row.get("R1", False))),
                        ry=bool(row.get("RY", row.get("R2", False))),
                        rz=bool(row.get("RZ", row.get("R3", False))),
                    )
                )

        # 3. Materials
        mat_table = (
            tables.get("MATERIAL PROPERTIES - GENERAL")
            or tables.get("MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES")
            or tables.get("MATERIAL PROPERTIES")
        )
        if mat_table:
            for row in mat_table.rows:
                mid = str(row.get("Material", row.get("MatName", "")))
                if not mid:
                    continue
                E = float(row.get("E", row.get("E1", 2.9e7)))
                nu = float(row.get("Nu", row.get("U12", 0.3)))
                rho = float(row.get("UnitMass", row.get("MassDensity", 0.0)))
                materials.append(ElasticMaterial(id=mid, E=E, nu=nu, rho=rho))

        if not materials:
            materials.append(ElasticMaterial(id="CONC", E=2.5e7, nu=0.2, rho=2400.0))

        # 4. Frame Sections
        sec_table = tables.get("FRAME SECTION PROPERTIES") or tables.get("FRAME SECTION PROPERTIES 01 - GENERAL") or tables.get("FRAME SECTIONS")
        if sec_table:
            for row in sec_table.rows:
                sname = str(row.get("SectionName", row.get("Section", "")))
                if not sname:
                    continue
                shape = str(row.get("Shape", "")).upper()
                t3 = float(row.get("t3", row.get("Depth", 0.0)))
                t2 = float(row.get("t2", row.get("Width", 0.0)))
                A = float(row.get("Area", row.get("A", 0.0)))
                I33 = float(row.get("I33", row.get("Iz", 0.0)))
                I22 = float(row.get("I22", row.get("Iy", 0.0)))
                J = float(row.get("J", 0.0))

                if shape == "RECTANGULAR" or (t3 > 0 and t2 > 0 and shape in ("", "CONCRETE", "RECT")):
                    sections.append(RectangularSection(id=sname, b=t2, h=t3))
                elif shape == "CIRCULAR" or (t3 > 0 and shape == "CIRCLE"):
                    sections.append(CircularSection(id=sname, d=t3))
                else:
                    sections.append(
                        GeneralSection(
                            id=sname,
                            A=A if A > 0 else 1.0,
                            Iz=I33 if I33 > 0 else 1.0,
                            Iy=I22 if I22 > 0 else 1.0,
                            J=J if J > 0 else 1.0,
                        )
                    )

        if not sections:
            sections.append(RectangularSection(id="DEFAULT_SEC", b=0.4, h=0.4))

        # 5. Line Connectivity -> Elements
        # Tables: "LINE CONNECTIVITY" or "CONNECTIVITY - FRAME"
        conn_table = tables.get("LINE CONNECTIVITY") or tables.get("CONNECTIVITY - FRAME")
        default_mat_id = materials[0].id
        default_sec_id = sections[0].id

        if conn_table:
            for row in conn_table.rows:
                lid = str(row.get("Line", row.get("Frame", row.get("LineID", ""))))
                pi = str(row.get("PointI", row.get("JointI", "")))
                pj = str(row.get("PointJ", row.get("JointJ", "")))
                if not lid or not pi or not pj:
                    continue
                sec_id = str(row.get("SectionName", default_sec_id))
                elements.append(
                    BeamElement(
                        id=lid,
                        start_node=pi,
                        end_node=pj,
                        material_id=default_mat_id,
                        section_id=sec_id,
                    )
                )

        # 6. Point Loads
        pt_loads_table = tables.get("POINT LOADS - FORCE") or tables.get("JOINT LOADS - FORCE")
        if pt_loads_table:
            for row in pt_loads_table.rows:
                pid = str(row.get("Point", row.get("Joint", "")))
                pat = str(row.get("LoadPat", row.get("LoadCase", "DEAD")))
                if pat not in load_cases_dict:
                    load_cases_dict[pat] = LoadCase(id=pat)

                load_cases_dict[pat].point_loads.append(
                    PointLoad(
                        node_id=pid,
                        fx=float(row.get("FX", row.get("F1", 0.0))),
                        fy=float(row.get("FY", row.get("F2", 0.0))),
                        fz=float(row.get("FZ", row.get("F3", 0.0))),
                        mx=float(row.get("MX", row.get("M1", 0.0))),
                        my=float(row.get("MY", row.get("M2", 0.0))),
                        mz=float(row.get("MZ", row.get("M3", 0.0))),
                    )
                )

        return StructuralModel(
            nodes=nodes,
            elements=elements,
            materials=materials,
            sections=sections,
            supports=supports,
            load_cases=list(load_cases_dict.values()),
        )
