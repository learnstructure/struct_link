"""
SAP2000 .s2k file reader.

Parses SAP2000 text files (.s2k / .$2k) into struct_core.StructuralModel instances.
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


class Sap2000FileReader(BaseModelReader):
    """Imports a SAP2000 .s2k text file into a struct_core.StructuralModel."""

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

        # 1. Joint Coordinates -> Nodes
        # Table names: "JOINT COORDINATES"
        coord_table = tables.get("JOINT COORDINATES")
        if coord_table:
            for row in coord_table.rows:
                jid = str(row.get("Joint", row.get("JointID", "")))
                if not jid:
                    continue
                x = float(row.get("XorR", row.get("X", 0.0)))
                y = float(row.get("Y", 0.0))
                z = float(row.get("Z", 0.0))
                nodes.append(Node(id=jid, x=x, y=y, z=z))

        # 2. Joint Restraints -> Supports
        # Table: "JOINT RESTRAINTS"
        restr_table = tables.get("JOINT RESTRAINTS")
        if restr_table:
            for row in restr_table.rows:
                jid = str(row.get("Joint", ""))
                if not jid:
                    continue
                supports.append(
                    Support(
                        node_id=jid,
                        ux=bool(row.get("U1", False)),
                        uy=bool(row.get("U2", False)),
                        uz=bool(row.get("U3", False)),
                        rx=bool(row.get("R1", False)),
                        ry=bool(row.get("R2", False)),
                        rz=bool(row.get("R3", False)),
                    )
                )

        # 3. Materials
        # Table: "MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES" or "MATERIAL PROPERTIES"
        mat_table = tables.get("MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES") or tables.get("MATERIAL PROPERTIES")
        if mat_table:
            for row in mat_table.rows:
                mid = str(row.get("Material", row.get("MatName", "")))
                if not mid:
                    continue
                E = float(row.get("E1", row.get("E", 2.9e7)))
                nu = float(row.get("U12", row.get("Nu", 0.3)))
                rho = float(row.get("UnitMass", row.get("MassDensity", 0.0)))
                materials.append(ElasticMaterial(id=mid, E=E, nu=nu, rho=rho))

        if not materials:
            # Fallback default material if not in file
            materials.append(ElasticMaterial(id="CONC", E=2.5e7, nu=0.2, rho=2400.0))

        # 4. Frame Sections
        # Table: "FRAME SECTION PROPERTIES 01 - GENERAL" or "FRAME SECTIONS"
        sec_table = tables.get("FRAME SECTION PROPERTIES 01 - GENERAL") or tables.get("FRAME SECTIONS")
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

        # Check for section assignments if separated: "FRAME SECTION ASSIGNMENTS"
        frame_sec_map: Dict[str, str] = {}
        sec_assign_table = tables.get("FRAME SECTION ASSIGNMENTS")
        if sec_assign_table:
            for row in sec_assign_table.rows:
                fid = str(row.get("Frame", ""))
                sname = str(row.get("SectionName", row.get("AnalSect", "")))
                if fid and sname:
                    frame_sec_map[fid] = sname

        # 5. Connectivity - Frame -> Elements
        # Table: "CONNECTIVITY - FRAME"
        conn_table = tables.get("CONNECTIVITY - FRAME")
        default_mat_id = materials[0].id
        default_sec_id = sections[0].id

        if conn_table:
            for row in conn_table.rows:
                fid = str(row.get("Frame", row.get("FrameID", "")))
                ji = str(row.get("JointI", ""))
                jj = str(row.get("JointJ", ""))
                if not fid or not ji or not jj:
                    continue
                sec_id = frame_sec_map.get(fid, default_sec_id)
                elements.append(
                    BeamElement(
                        id=fid,
                        start_node=ji,
                        end_node=jj,
                        material_id=default_mat_id,
                        section_id=sec_id,
                    )
                )

        # 6. Joint Loads -> LoadCases with PointLoads
        # Table: "JOINT LOADS - FORCE"
        jt_loads_table = tables.get("JOINT LOADS - FORCE")
        if jt_loads_table:
            for row in jt_loads_table.rows:
                jid = str(row.get("Joint", ""))
                pat = str(row.get("LoadPat", row.get("LoadCase", "DEAD")))
                if pat not in load_cases_dict:
                    load_cases_dict[pat] = LoadCase(id=pat)

                fx = float(row.get("F1", row.get("Fx", 0.0)))
                fy = float(row.get("F2", row.get("Fy", 0.0)))
                fz = float(row.get("F3", row.get("Fz", 0.0)))
                mx = float(row.get("M1", row.get("Mx", 0.0)))
                my = float(row.get("M2", row.get("My", 0.0)))
                mz = float(row.get("M3", row.get("Mz", 0.0)))

                load_cases_dict[pat].point_loads.append(
                    PointLoad(
                        node_id=jid,
                        fx=fx,
                        fy=fy,
                        fz=fz,
                        mx=mx,
                        my=my,
                        mz=mz,
                    )
                )

        # 7. Frame Loads -> DistributedLoads
        # Table: "FRAME LOADS - DISTRIBUTED"
        frm_loads_table = tables.get("FRAME LOADS - DISTRIBUTED")
        if frm_loads_table:
            for row in frm_loads_table.rows:
                fid = str(row.get("Frame", ""))
                pat = str(row.get("LoadPat", "DEAD"))
                if pat not in load_cases_dict:
                    load_cases_dict[pat] = LoadCase(id=pat)

                dir_name = str(row.get("Dir", row.get("Direction", "Gravity"))).upper()
                val = float(row.get("FVal", row.get("Val1", row.get("F1", 0.0))))

                # Map direction
                wx, wy, wz = 0.0, 0.0, 0.0
                if "GRAV" in dir_name or dir_name in ("Z", "3", "-Z"):
                    wz = -abs(val) if "GRAV" in dir_name else val
                elif dir_name in ("Y", "2"):
                    wy = val
                elif dir_name in ("X", "1"):
                    wx = val

                load_cases_dict[pat].element_loads.append(
                    DistributedLoad(
                        element_id=fid,
                        wx=wx,
                        wy=wy,
                        wz=wz,
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
