"""
Base CSI (.s2k / .e2k) file reader.

Unified parser for Computers & Structures, Inc. text database files (SAP2000 .s2k, ETABS .e2k).
"""

from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from struct_core import (
    BeamElement,
    CircularSection,
    DistributedLoad,
    ElasticMaterial,
    GeneralSection,
    LoadCase,
    MassSource,
    MassSourceFactor,
    ModalAnalysis,
    Node,
    PointLoad,
    Project,
    RectangularSection,
    ResponseSpectrumAnalysis,
    StructuralModel,
    Support,
)

from ...base import BaseModelReader
from .s2k_parser import S2kParser, S2kTable

logger = logging.getLogger(__name__)


class CsiFileReader(BaseModelReader):
    """
    Unified reader for CSI text data files (SAP2000 .s2k and ETABS .e2k).
    Parses both SAP2000 and ETABS table and column naming conventions,
    as well as native ETABS dollar command text formats ($ SECTION).
    """

    @classmethod
    def _is_dollar_e2k(cls, content: str) -> bool:
        """Detect whether text content uses native ETABS dollar command syntax."""
        lines = content.splitlines()[:50]
        for l in lines:
            s = l.strip().upper()
            if s.startswith("$") and any(
                keyword in s for keyword in ("PROGRAM", "STORIES", "CONTROLS", "POINT COORDINATES")
            ):
                return True
        return False

    def read_model(self, source: Union[str, Path]) -> StructuralModel:
        path = Path(source)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        if self._is_dollar_e2k(content):
            proj = self._build_project_from_dollar_e2k(content)
            return proj.model

        tables = S2kParser.parse_string(content)
        return self._build_model(tables)

    def read_project(self, source: Union[str, Path], title: Optional[str] = None) -> Project:
        path = Path(source)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        file_title = title or (path.stem if hasattr(path, "stem") else str(path))

        if self._is_dollar_e2k(content):
            return self._build_project_from_dollar_e2k(content, title=file_title)

        tables = S2kParser.parse_string(content)
        model = self._build_model(tables)
        project = Project(model=model)
        project.metadata.title = file_title

        # Extract units from PROGRAM CONTROL or PROGRAM INFORMATION if defined
        prog_table = tables.get("PROGRAM CONTROL") or tables.get("PROGRAM INFORMATION")
        if prog_table and prog_table.rows:
            curr_units = prog_table.rows[0].get("CurrUnits")
            if curr_units and isinstance(curr_units, str):
                try:
                    from struct_core import parse_units

                    parsed_units = parse_units(curr_units)
                    project.metadata.units = parsed_units
                    if project.model:
                        project.model.units = parsed_units
                except Exception as exc:
                    logger.warning(
                        "Could not parse CSI CurrUnits '%s': %s", curr_units, exc
                    )

        return project

    def _build_model(self, tables: Dict[str, S2kTable]) -> StructuralModel:
        nodes: List[Node] = []
        supports: List[Support] = []
        materials: List[ElasticMaterial] = []
        sections: List[Union[RectangularSection, CircularSection, GeneralSection]] = []
        elements: List[BeamElement] = []
        load_cases_dict: Dict[str, LoadCase] = {}

        # 1. Coordinates -> Nodes
        # Supported tables: "JOINT COORDINATES" (SAP2000) or "POINT COORDINATES" (ETABS)
        coord_table = tables.get("JOINT COORDINATES") or tables.get("POINT COORDINATES")
        if coord_table:
            for row in coord_table.rows:
                jid = str(row.get("Joint", row.get("Point", row.get("JointID", ""))))
                if not jid:
                    continue
                x = float(row.get("XorR", row.get("X", 0.0)))
                y = float(row.get("Y", 0.0))
                z = float(row.get("Z", 0.0))
                nodes.append(Node(id=jid, x=x, y=y, z=z))

        # 2. Restraints -> Supports
        # Supported tables: "JOINT RESTRAINT ASSIGNMENTS", "POINT ASSIGNMENTS - RESTRAINTS", "JOINT RESTRAINTS"
        restr_table = (
            tables.get("JOINT RESTRAINT ASSIGNMENTS")
            or tables.get("POINT ASSIGNMENTS - RESTRAINTS")
            or tables.get("JOINT RESTRAINTS")
        )
        if restr_table:
            for row in restr_table.rows:
                jid = str(row.get("Joint", row.get("Point", row.get("JointID", ""))))
                if not jid:
                    continue
                supports.append(
                    Support(
                        node_id=jid,
                        ux=bool(row.get("U1", row.get("UX", False))),
                        uy=bool(row.get("U2", row.get("UY", False))),
                        uz=bool(row.get("U3", row.get("UZ", False))),
                        rx=bool(row.get("R1", row.get("RX", False))),
                        ry=bool(row.get("R2", row.get("RY", False))),
                        rz=bool(row.get("R3", row.get("RZ", False))),
                    )
                )

        # 3. Materials
        mat_info: Dict[str, Dict[str, str]] = {}
        mat_gen_table = tables.get("MATERIAL PROPERTIES 01 - GENERAL") or tables.get("MATERIAL PROPERTIES - GENERAL")
        if mat_gen_table:
            for row in mat_gen_table.rows:
                mid = str(row.get("Material", ""))
                if mid:
                    mat_info[mid] = {
                        "type": str(row.get("Type", "")),
                        "sym_type": str(row.get("SymType", "")),
                    }

        mat_table = (
            tables.get("MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES")
            or tables.get("MATERIAL PROPERTIES - GENERAL")
            or tables.get("MATERIAL PROPERTIES")
        )
        if mat_table:
            for row in mat_table.rows:
                mid = str(row.get("Material", row.get("MatName", "")))
                if not mid:
                    continue
                E = float(row.get("E1", row.get("E", 2.9e7)))
                nu = float(row.get("U12", row.get("Nu", 0.3)))
                rho = float(row.get("UnitMass", row.get("MassDensity", 0.0)))
                unit_weight = None
                if "UnitWeight" in row and row["UnitWeight"] is not None:
                    try:
                        unit_weight = float(row["UnitWeight"])
                    except (ValueError, TypeError):
                        pass
                m_type = mat_info.get(mid, {}).get("type")
                materials.append(ElasticMaterial(id=mid, name=m_type, E=E, nu=nu, rho=rho, unit_weight=unit_weight))

        if not materials:
            if (
                tables.get("FRAME SECTION PROPERTIES 01 - GENERAL")
                or tables.get("FRAME SECTION PROPERTIES")
                or tables.get("FRAME SECTIONS")
                or tables.get("CONNECTIVITY - FRAME")
                or tables.get("LINE CONNECTIVITY")
            ):
                materials.append(ElasticMaterial(id="CONC", name="Concrete", E=2.5e7, nu=0.2, rho=2400.0 / 9.80665, unit_weight=24.0))

        # 4. Frame Sections
        sec_mat_map: Dict[str, str] = {}
        sec_table = (
            tables.get("FRAME SECTION PROPERTIES 01 - GENERAL")
            or tables.get("FRAME SECTION PROPERTIES")
            or tables.get("FRAME SECTIONS")
        )
        if sec_table:
            for row in sec_table.rows:
                sname = str(row.get("SectionName", row.get("Section", "")))
                if not sname:
                    continue
                mat_name = str(row.get("Material", row.get("MatName", "")))
                if mat_name:
                    sec_mat_map[sname] = mat_name

                shape = str(row.get("Shape", "")).upper()
                t3 = float(row.get("t3", row.get("Depth", 0.0)))
                t2 = float(row.get("t2", row.get("Width", 0.0)))
                A = float(row.get("Area", row.get("A", 0.0)))
                I33 = float(row.get("I33", row.get("Iz", 0.0)))
                I22 = float(row.get("I22", row.get("Iy", 0.0)))
                J = float(row.get("J", row.get("TorsConst", 0.0)))

                as2 = float(row.get("AS2", row.get("As2", 0.0)))
                as3 = float(row.get("AS3", row.get("As3", 0.0)))

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
                            As2=as2 if as2 > 0 else None,
                            As3=as3 if as3 > 0 else None,
                        )
                    )

        if not sections:
            sections.append(RectangularSection(id="DEFAULT_SEC", b=0.4, h=0.4))

        # Check for section assignments
        frame_sec_map: Dict[str, str] = {}
        sec_assign_table = tables.get("FRAME SECTION ASSIGNMENTS")
        if sec_assign_table:
            for row in sec_assign_table.rows:
                fid = str(row.get("Frame", row.get("Line", "")))
                sname = str(row.get("SectionName", row.get("AnalSect", "")))
                if fid and sname:
                    frame_sec_map[fid] = sname

        # 5. Connectivity -> Elements
        # Supported tables: "CONNECTIVITY - FRAME" (SAP2000) or "LINE CONNECTIVITY" (ETABS)
        conn_table = tables.get("CONNECTIVITY - FRAME") or tables.get("LINE CONNECTIVITY")
        default_mat_id = materials[0].id
        default_sec_id = sections[0].id
        valid_mat_ids = {m.id for m in materials}

        # Parse Local Axes Assignments
        frame_angle_map: Dict[str, float] = {}
        axes_table = (
            tables.get("FRAME LOCAL AXES ASSIGNMENTS 1 - TYPICAL")
            or tables.get("FRAME LOCAL AXES ASSIGNMENTS")
            or tables.get("LINE ASSIGNMENTS - LOCAL AXES")
        )
        if axes_table:
            for row in axes_table.rows:
                fid = str(row.get("Frame", row.get("Line", "")))
                ang_val = row.get("Angle")
                if fid and ang_val is not None:
                    try:
                        frame_angle_map[fid] = float(ang_val)
                    except (ValueError, TypeError):
                        pass

        nodes_dict = {str(n.id): n for n in nodes}

        if conn_table:
            for row in conn_table.rows:
                fid = str(row.get("Frame", row.get("Line", row.get("FrameID", row.get("LineID", "")))))
                ji = str(row.get("JointI", row.get("PointI", "")))
                jj = str(row.get("JointJ", row.get("PointJ", "")))
                if not fid or not ji or not jj:
                    continue
                sec_id = row.get("SectionName") or frame_sec_map.get(fid, default_sec_id)
                mat_id = sec_mat_map.get(sec_id, default_mat_id)
                if mat_id not in valid_mat_ids:
                    mat_id = default_mat_id

                # Calculate roll_angle from CSI Angle
                # In fem3d:
                # - For horizontal members: default matches CSI Angle = 0.
                # - For vertical members (+Z): fem3d default has v2=+Y, v3=-X,
                #   which corresponds to CSI Angle = 90.
                csi_angle = frame_angle_map.get(fid, 0.0)
                ni = nodes_dict.get(ji)
                nj = nodes_dict.get(jj)
                if ni and nj:
                    dx = float(nj.x - ni.x)
                    dy = float(nj.y - ni.y)
                    dz = float(getattr(nj, "z", 0.0) - getattr(ni, "z", 0.0))
                    is_vertical = (dx**2 + dy**2) < 1e-12 and abs(dz) > 1e-12
                    if is_vertical:
                        roll_angle = csi_angle - 90.0
                    else:
                        roll_angle = csi_angle
                else:
                    roll_angle = csi_angle

                # Normalize roll_angle to (-180, 180]
                roll_angle = (roll_angle + 180.0) % 360.0 - 180.0
                if abs(roll_angle) < 1e-6:
                    roll_angle = 0.0

                elements.append(
                    BeamElement(
                        id=fid,
                        start_node=ji,
                        end_node=jj,
                        material_id=mat_id,
                        section_id=sec_id,
                        roll_angle=roll_angle,
                    )
                )

        # 6. Load Patterns
        pat_table = tables.get("LOAD PATTERN DEFINITIONS") or tables.get("LOAD CASE DEFINITIONS")
        if pat_table:
            for row in pat_table.rows:
                pat = str(row.get("LoadPat", row.get("Case", row.get("LoadCase", ""))))
                if pat and pat not in load_cases_dict:
                    self_wt = float(row.get("SelfWtMult", 0.0)) > 0.0
                    auto_ld = str(row.get("AutoLoad", "")).strip() or None
                    if auto_ld and auto_ld.lower() in ("none", ""):
                        auto_ld = None
                    load_cases_dict[pat] = LoadCase(id=pat, include_self_weight=self_wt, auto_load=auto_ld)

        # Parse AUTO SEISMIC - ASCE 7-22 parameters if present
        auto_seis_tbl = tables.get("AUTO SEISMIC - ASCE 7-22")
        if auto_seis_tbl:
            for row in auto_seis_tbl.rows:
                pat = str(row.get("LoadPat", ""))
                if pat in load_cases_dict:
                    load_cases_dict[pat].auto_load = "ASCE 7-22"
                    load_cases_dict[pat].seismic_params = {
                        "direction": str(row.get("Dir", "X")),
                        "sds": float(row.get("Sds", 1.0)),
                        "sd1": float(row.get("Sd1", 0.4)),
                        "s1": float(row.get("S1", 0.35)) if row.get("S1") is not None else 0.35,
                        "tl": float(row.get("TL", 8.0)),
                        "r_factor": float(row.get("R", 8.0)),
                        "importance_factor": float(row.get("I", 1.0)),
                        "site_class": str(row.get("SiteClass", "D")),
                        "base_shear": float(row.get("BaseShear", 0.0)) if row.get("BaseShear") is not None else None,
                        "weight_used": float(row.get("WeightUsed", 0.0)) if row.get("WeightUsed") is not None else None,
                        "period_used": float(row.get("TUsed", 0.0)) if row.get("TUsed") is not None else None,
                        "coeff_used": float(row.get("CoeffUsed", 0.0)) if row.get("CoeffUsed") is not None else None,
                    }

        # 7. Joint / Point Loads -> PointLoads
        primary_pt_table = tables.get("POINT LOADS - FORCE") or tables.get("JOINT LOADS - FORCE")
        pt_tables = [primary_pt_table] if primary_pt_table else []
        if "AUTO SEISMIC LOADS TO JOINTS" in tables:
            pt_tables.append(tables["AUTO SEISMIC LOADS TO JOINTS"])

        for jt_loads_table in pt_tables:
            if jt_loads_table:
                for row in jt_loads_table.rows:
                    jid = str(row.get("Joint", row.get("Point", "")))
                    pat = str(row.get("LoadPat", row.get("LoadCase", "DEAD")))
                    if pat not in load_cases_dict:
                        load_cases_dict[pat] = LoadCase(id=pat)

                    fx = float(row.get("F1", row.get("Fx", row.get("FX", 0.0))))
                    fy = float(row.get("F2", row.get("Fy", row.get("FY", 0.0))))
                    fz = float(row.get("F3", row.get("Fz", row.get("FZ", 0.0))))
                    mx = float(row.get("M1", row.get("Mx", row.get("MX", 0.0))))
                    my = float(row.get("M2", row.get("My", row.get("MY", 0.0))))
                    mz = float(row.get("M3", row.get("Mz", row.get("MZ", 0.0))))

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

        # 8. Frame / Line Loads -> DistributedLoads
        frm_loads_table = tables.get("FRAME LOADS - DISTRIBUTED") or tables.get("LINE LOADS - DISTRIBUTED")
        if frm_loads_table:
            for row in frm_loads_table.rows:
                fid = str(row.get("Frame", row.get("Line", "")))
                pat = str(row.get("LoadPat", "DEAD"))
                if pat not in load_cases_dict:
                    load_cases_dict[pat] = LoadCase(id=pat)

                val_a = None
                for k in ("FOverLA", "FValA", "FVal", "Val1", "F1", "MOverLA", "MValA", "MVal"):
                    if k in row and row[k] is not None:
                        try:
                            val_a = float(row[k])
                            break
                        except (ValueError, TypeError):
                            pass

                val_b = None
                for k in ("FOverLB", "FValB", "FVal", "Val2", "F2", "MOverLB", "MValB", "MVal"):
                    if k in row and row[k] is not None:
                        try:
                            val_b = float(row[k])
                            break
                        except (ValueError, TypeError):
                            pass

                if val_a is None and val_b is None:
                    val_a = 0.0
                    val_b = 0.0
                elif val_a is None:
                    val_a = val_b
                elif val_b is None:
                    val_b = val_a

                avg_val = (val_a + val_b) / 2.0
                dir_name = str(row.get("Dir", row.get("Direction", "Gravity"))).strip().upper()
                csys = str(row.get("CoordSys", "GLOBAL")).strip().upper()

                wx, wy, wz = 0.0, 0.0, 0.0
                w1, w2, w3 = 0.0, 0.0, 0.0
                is_local = False
                w_start: Optional[float] = None
                w_end: Optional[float] = None

                if "GRAV" in dir_name:
                    wz = -abs(avg_val)
                    w_start = -abs(val_a)
                    w_end = -abs(val_b)
                elif dir_name in ("Z", "+Z"):
                    wz = avg_val
                    w_start = val_a
                    w_end = val_b
                elif dir_name in ("-Z",):
                    wz = -avg_val
                    w_start = -val_a
                    w_end = -val_b
                elif dir_name in ("Y", "+Y"):
                    wy = avg_val
                    w_start = val_a
                    w_end = val_b
                elif dir_name in ("-Y",):
                    wy = -avg_val
                    w_start = -val_a
                    w_end = -val_b
                elif dir_name in ("X", "+X"):
                    wx = avg_val
                    w_start = val_a
                    w_end = val_b
                elif dir_name in ("-X",):
                    wx = -avg_val
                    w_start = -val_a
                    w_end = -val_b
                elif dir_name in ("1", "+1") or (csys == "LOCAL" and dir_name in ("X", "+X")):
                    is_local = True
                    w1 = avg_val
                    w_start = val_a
                    w_end = val_b
                elif dir_name in ("-1",) or (csys == "LOCAL" and dir_name in ("-X",)):
                    is_local = True
                    w1 = -avg_val
                    w_start = -val_a
                    w_end = -val_b
                elif dir_name in ("2", "+2") or (csys == "LOCAL" and dir_name in ("Y", "+Y")):
                    is_local = True
                    w2 = avg_val
                    w_start = val_a
                    w_end = val_b
                elif dir_name in ("-2",) or (csys == "LOCAL" and dir_name in ("-Y",)):
                    is_local = True
                    w2 = -avg_val
                    w_start = -val_a
                    w_end = -val_b
                elif dir_name in ("3", "+3") or (csys == "LOCAL" and dir_name in ("Z", "+Z")):
                    is_local = True
                    w3 = avg_val
                    w_start = val_a
                    w_end = val_b
                elif dir_name in ("-3",) or (csys == "LOCAL" and dir_name in ("-Z",)):
                    is_local = True
                    w3 = -avg_val
                    w_start = -val_a
                    w_end = -val_b
                else:
                    raise ValueError(
                        f"Unsupported load direction '{dir_name}' (coordinate system '{csys}') in CSI file."
                    )

                load_kwargs = {
                    "element_id": fid,
                    "wx": wx,
                    "wy": wy,
                    "wz": wz,
                    "is_local": is_local,
                }
                if is_local:
                    load_kwargs["w1"] = w1
                    load_kwargs["w2"] = w2
                    load_kwargs["w3"] = w3
                if val_a != val_b:
                    load_kwargs["w_start"] = w_start
                    load_kwargs["w_end"] = w_end

                load_cases_dict[pat].element_loads.append(
                    DistributedLoad(**load_kwargs)
                )

        # 9. Mass Source
        mass_source: Optional[MassSource] = None
        ms_table = tables.get("MASS SOURCE")
        if ms_table and ms_table.rows:
            first_row = ms_table.rows[0]
            ms_id = str(first_row.get("MassSource", "MSSSRC1"))
            inc_elem = False
            factors: List[MassSourceFactor] = []
            for row in ms_table.rows:
                elem_val = str(row.get("Elements", "")).strip().lower()
                if elem_val in ("yes", "true", "1"):
                    inc_elem = True
                pat = str(row.get("LoadPat", "")).strip()
                if pat:
                    mult = float(row.get("Multiplier", 0.0)) if row.get("Multiplier") is not None else 0.0
                    if pat.upper() == "DEAD" and mult > 0.0 and not inc_elem:
                        inc_elem = True
                    factors.append(MassSourceFactor(load_case_id=pat, factor=mult))
            mass_source = MassSource(
                id=ms_id,
                name=ms_id,
                include_element_mass=inc_elem,
                factors=factors,
            )

        # 10. Modal Analysis Cases
        analysis_cases = []
        modal_table = tables.get("CASE - MODAL 1 - GENERAL")
        if modal_table:
            for row in modal_table.rows:
                cid = str(row.get("Case", "MODAL"))
                num_modes = int(row.get("MaxNumModes", 12))
                analysis_cases.append(ModalAnalysis(id=cid, num_modes=num_modes))

        # 11. Response Spectrum Analysis Cases
        rsa_table = tables.get("CASE - RESPONSE SPECTRUM 1 - GENERAL")
        rsa_assign = tables.get("CASE - RESPONSE SPECTRUM 2 - LOAD ASSIGNMENTS")
        if rsa_table:
            for row in rsa_table.rows:
                cid = str(row.get("Case", "RSx"))
                combo = str(row.get("ModalCombo", "CQC"))
                damp = float(row.get("ConstDamp", 0.05)) if row.get("ConstDamp") is not None else 0.05
                dir_val = "X"
                sf_val = 9.80665
                fn_name = "asce7-22"
                if rsa_assign:
                    for a_row in rsa_assign.rows:
                        if str(a_row.get("Case", "")) == cid:
                            lname = str(a_row.get("LoadName", "U1"))
                            dir_val = "Y" if "U2" in lname else ("Z" if "U3" in lname else "X")
                            if a_row.get("TransAccSF"):
                                sf_val = float(a_row.get("TransAccSF"))
                            if a_row.get("Function"):
                                fn_name = str(a_row.get("Function"))
                            break
                analysis_cases.append(
                    ResponseSpectrumAnalysis(
                        id=cid,
                        name=cid,
                        direction=dir_val,
                        combination=combo,
                        damping_ratio=damp,
                        scale_factor=sf_val,
                        function_name=fn_name,
                    )
                )

        return StructuralModel(
            nodes=nodes,
            elements=elements,
            materials=materials,
            sections=sections,
            supports=supports,
            load_cases=list(load_cases_dict.values()),
            analysis_cases=analysis_cases,
            mass_source=mass_source,
            mass_sources=[mass_source] if mass_source else [],
        )

    def _build_project_from_dollar_e2k(self, content: str, title: Optional[str] = None) -> Project:
        """Parse native ETABS text data ($ SECTION command syntax) into a Project."""
        sections: Dict[str, List[str]] = {}
        current_sec: Optional[str] = None
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("$"):
                current_sec = stripped[1:].strip().upper()
                sections[current_sec] = []
            elif current_sec and stripped:
                sections[current_sec].append(stripped)

        model = StructuralModel()
        project = Project(model=model)

        # 1. Controls / Units
        units_str = "kN, m"
        ctrl_lines = sections.get("CONTROLS", [])
        for l in ctrl_lines:
            tokens = shlex.split(l)
            if tokens and tokens[0].upper() == "UNITS":
                f_u = tokens[1].upper() if len(tokens) > 1 else "KN"
                l_u = tokens[2].upper() if len(tokens) > 2 else "M"
                units_str = f"{f_u.lower()}, {l_u.lower()}"
            if tokens and tokens[0].upper() == "TITLE1" and not title:
                title = tokens[1]

        if title:
            project.metadata.title = title
        try:
            from struct_core import parse_units
            parsed_units = parse_units(units_str)
            project.metadata.units = parsed_units
            model.units = parsed_units
        except Exception:
            model.units = units_str

        # 2. Stories
        story_lines = sections.get("STORIES - IN SEQUENCE FROM TOP", [])
        story_raw: List[Tuple[str, float]] = []
        base_elev = 0.0
        for l in story_lines:
            tokens = shlex.split(l)
            if len(tokens) >= 2 and tokens[0].upper() == "STORY":
                sname = tokens[1]
                if sname.upper() == "BASE":
                    for i in range(2, len(tokens) - 1):
                        if tokens[i].upper() == "ELEV":
                            base_elev = float(tokens[i + 1])
                else:
                    h_val = 0.0
                    for i in range(2, len(tokens) - 1):
                        if tokens[i].upper() == "HEIGHT":
                            h_val = float(tokens[i + 1])
                    story_raw.append((sname, h_val))

        story_order = ["Base"]
        story_elev: Dict[str, float] = {"Base": base_elev}
        curr_z = base_elev
        for sname, h in reversed(story_raw):
            curr_z += h
            story_order.append(sname)
            story_elev[sname] = curr_z
        model.story_elevations = [story_elev[s] for s in story_order if s != "Base"]

        # 3. Materials
        mat_lines = sections.get("MATERIAL PROPERTIES", [])
        mat_dict: Dict[str, Dict[str, Any]] = {}
        for l in mat_lines:
            tokens = shlex.split(l)
            if len(tokens) >= 2 and tokens[0].upper() == "MATERIAL":
                mid = tokens[1]
                if mid not in mat_dict:
                    mat_dict[mid] = {"id": mid, "E": 30.0e6, "nu": 0.2, "unit_weight": 24.0}
                for i in range(2, len(tokens)):
                    key = tokens[i].upper()
                    if key == "WEIGHTPERVOLUME" and i + 1 < len(tokens):
                        mat_dict[mid]["unit_weight"] = float(tokens[i + 1])
                    elif key == "E" and i + 1 < len(tokens):
                        mat_dict[mid]["E"] = float(tokens[i + 1])
                    elif key == "U" and i + 1 < len(tokens):
                        mat_dict[mid]["nu"] = float(tokens[i + 1])
                    elif key == "FC" and i + 1 < len(tokens):
                        mat_dict[mid]["fc"] = float(tokens[i + 1])

        for mid, mdata in mat_dict.items():
            mat = ElasticMaterial(
                id=mid,
                E=mdata["E"],
                nu=mdata["nu"],
                unit_weight=mdata["unit_weight"],
                rho=mdata["unit_weight"] / 9.80665,
            )
            model.materials.append(mat)
        default_mat_id = model.materials[0].id if model.materials else "CONC"

        # 4. Frame Sections
        sec_lines = sections.get("FRAME SECTIONS", [])
        for l in sec_lines:
            tokens = shlex.split(l)
            if len(tokens) >= 2 and tokens[0].upper() == "FRAMESECTION":
                sec_id = tokens[1]
                shape = "RECTANGULAR"
                d = 0.4
                b = 0.3
                for i in range(2, len(tokens)):
                    key = tokens[i].upper()
                    if key == "SHAPE" and i + 1 < len(tokens):
                        shape = tokens[i + 1].upper()
                    elif key == "D" and i + 1 < len(tokens):
                        d = float(tokens[i + 1])
                    elif key == "B" and i + 1 < len(tokens):
                        b = float(tokens[i + 1])
                if "RECT" in shape:
                    model.sections.append(RectangularSection(id=sec_id, b=b, h=d))
                elif "CIRC" in shape:
                    model.sections.append(CircularSection(id=sec_id, d=d))

        # 5. Plan Points
        pt_lines = sections.get("POINT COORDINATES", [])
        plan_pts: Dict[str, Tuple[float, float]] = {}
        for l in pt_lines:
            tokens = shlex.split(l)
            if len(tokens) >= 4 and tokens[0].upper() == "POINT":
                pid = tokens[1]
                x = float(tokens[2])
                y = float(tokens[3])
                plan_pts[pid] = (x, y)

        # 6. Point Restraints
        restr_lines = sections.get("POINT ASSIGNS", [])
        restr_map: Dict[Tuple[str, str], List[str]] = {}
        for l in restr_lines:
            tokens = shlex.split(l)
            if len(tokens) >= 3 and tokens[0].upper() == "POINTASSIGN":
                pid = tokens[1]
                st_name = tokens[2]
                for i in range(3, len(tokens) - 1):
                    if tokens[i].upper() == "RESTRAINT":
                        restr_map[(pid, st_name)] = tokens[i + 1].upper().split()

        # 7. Line Connectivities
        line_lines = sections.get("LINE CONNECTIVITIES", [])
        line_conn: Dict[str, Tuple[str, str, str]] = {}
        for l in line_lines:
            tokens = shlex.split(l)
            if len(tokens) >= 5 and tokens[0].upper() == "LINE":
                lname = tokens[1]
                ltype = tokens[2].upper()
                pti = tokens[3]
                ptj = tokens[4]
                line_conn[lname] = (ltype, pti, ptj)

        # 8. Line Assigns
        assign_lines = sections.get("LINE ASSIGNS", [])
        line_assigns: List[Tuple[str, str, str, float]] = []
        for l in assign_lines:
            tokens = shlex.split(l)
            if len(tokens) >= 3 and tokens[0].upper() == "LINEASSIGN":
                lname = tokens[1]
                st_name = tokens[2]
                sec_id = ""
                ang = 0.0
                for i in range(3, len(tokens)):
                    key = tokens[i].upper()
                    if key == "SECTION" and i + 1 < len(tokens):
                        sec_id = tokens[i + 1]
                    elif key == "ANG" and i + 1 < len(tokens):
                        ang = float(tokens[i + 1])
                line_assigns.append((lname, st_name, sec_id, ang))

        # 9. Create 3D Nodes
        needed_nodes = set(restr_map.keys())
        for (lname, st_name, _, _) in line_assigns:
            if lname in line_conn:
                ltype, pti, ptj = line_conn[lname]
                needed_nodes.add((pti, st_name))
                needed_nodes.add((ptj, st_name))
                if ltype in ("COLUMN", "BRACE"):
                    k = story_order.index(st_name)
                    bot_st = story_order[k - 1]
                    needed_nodes.add((pti, bot_st))

        node_id_map: Dict[Tuple[str, str], int] = {}
        nid_counter = 1
        sorted_node_keys = sorted(
            list(needed_nodes),
            key=lambda k: (story_elev.get(k[1], 0.0), plan_pts.get(k[0], (0.0, 0.0))[1], plan_pts.get(k[0], (0.0, 0.0))[0]),
        )
        for pt_id, st_name in sorted_node_keys:
            if pt_id not in plan_pts or st_name not in story_elev:
                continue
            x, y = plan_pts[pt_id]
            z = story_elev[st_name]
            node = Node(id=nid_counter, x=x, y=y, z=z)
            model.nodes.append(node)
            node_id_map[(pt_id, st_name)] = nid_counter

            if (pt_id, st_name) in restr_map:
                restrs = restr_map[(pt_id, st_name)]
                model.supports.append(
                    Support(
                        node_id=nid_counter,
                        ux="UX" in restrs,
                        uy="UY" in restrs,
                        uz="UZ" in restrs,
                        rx="RX" in restrs,
                        ry="RY" in restrs,
                        rz="RZ" in restrs,
                    )
                )
            nid_counter += 1

        # 10. Create Elements
        elem_counter = 1
        elem_lookup: Dict[Tuple[str, str], int] = {}
        for lname, st_name, sec_id, ang in line_assigns:
            if lname not in line_conn:
                continue
            ltype, pti, ptj = line_conn[lname]
            if ltype in ("COLUMN", "BRACE"):
                k = story_order.index(st_name)
                bot_st = story_order[k - 1]
                if (pti, bot_st) not in node_id_map or (ptj if ltype == "BRACE" else pti, st_name) not in node_id_map:
                    continue
                n_start = node_id_map[(pti, bot_st)]
                n_end = node_id_map[(ptj if ltype == "BRACE" else pti, st_name)]
                eff_ang = (ang - 90.0 + 180.0) % 360.0 - 180.0 if ltype == "COLUMN" else ang
                elem = BeamElement(
                    id=elem_counter,
                    start_node=n_start,
                    end_node=n_end,
                    material_id=default_mat_id,
                    section_id=sec_id,
                    include_shear_deformation=True,
                    roll_angle=eff_ang,
                )
                model.elements.append(elem)
                elem_lookup[(lname, st_name)] = elem_counter
                elem_counter += 1
            elif ltype == "BEAM":
                if (pti, st_name) not in node_id_map or (ptj, st_name) not in node_id_map:
                    continue
                n_start = node_id_map[(pti, st_name)]
                n_end = node_id_map[(ptj, st_name)]
                elem = BeamElement(
                    id=elem_counter,
                    start_node=n_start,
                    end_node=n_end,
                    material_id=default_mat_id,
                    section_id=sec_id,
                    include_shear_deformation=True,
                    roll_angle=ang,
                )
                model.elements.append(elem)
                elem_lookup[(lname, st_name)] = elem_counter
                elem_counter += 1

        # 11. Load Patterns
        pat_lines = sections.get("LOAD PATTERNS", [])
        lc_map: Dict[str, LoadCase] = {}
        for l in pat_lines:
            tokens = shlex.split(l)
            if not tokens:
                continue
            if tokens[0].upper() == "LOADPATTERN":
                lc_id = tokens[1]
                sw = False
                for i in range(2, len(tokens) - 1):
                    if tokens[i].upper() == "SELFWEIGHT":
                        sw = float(tokens[i + 1]) > 0
                lc = LoadCase(id=lc_id, name=lc_id, include_self_weight=sw)
                model.load_cases.append(lc)
                lc_map[lc_id] = lc
            elif tokens[0].upper() == "SEISMIC":
                if len(tokens) >= 3:
                    lc_id = tokens[1]
                    code_name = tokens[2]
                    sparams: Dict[str, Any] = {"code": code_name}
                    i = 3
                    while i < len(tokens):
                        k = tokens[i].upper()
                        if i + 1 < len(tokens):
                            v = tokens[i + 1]
                            try:
                                v_num = float(v)
                                if v_num.is_integer():
                                    v_num = int(v_num)
                                sparams[k.lower()] = v_num
                            except ValueError:
                                sparams[k.lower()] = v
                            i += 2
                        else:
                            i += 1
                    if lc_id in lc_map:
                        lc_map[lc_id].auto_load = code_name
                        lc_map[lc_id].seismic_params = sparams

        # 12. Point Loads
        ptld_lines = sections.get("POINT OBJECT LOADS", [])
        for l in ptld_lines:
            tokens = shlex.split(l)
            if len(tokens) >= 4 and tokens[0].upper() == "POINTLOAD":
                pid = tokens[1]
                st_name = tokens[2]
                lc_id = ""
                fx = fy = fz = mx = my = mz = 0.0
                for i in range(3, len(tokens)):
                    key = tokens[i].upper()
                    if key == "LC" and i + 1 < len(tokens): lc_id = tokens[i + 1]
                    elif key == "FX" and i + 1 < len(tokens): fx = float(tokens[i + 1])
                    elif key == "FY" and i + 1 < len(tokens): fy = float(tokens[i + 1])
                    elif key == "FZ" and i + 1 < len(tokens): fz = float(tokens[i + 1])
                    elif key == "MX" and i + 1 < len(tokens): mx = float(tokens[i + 1])
                    elif key == "MY" and i + 1 < len(tokens): my = float(tokens[i + 1])
                    elif key == "MZ" and i + 1 < len(tokens): mz = float(tokens[i + 1])

                if lc_id in lc_map and (pid, st_name) in node_id_map:
                    nid = node_id_map[(pid, st_name)]
                    lc_map[lc_id].point_loads.append(
                        PointLoad(node_id=nid, fx=fx, fy=fy, fz=fz, mx=mx, my=my, mz=mz)
                    )

        # 13. Frame Loads
        frld_lines = sections.get("FRAME OBJECT LOADS", [])
        for l in frld_lines:
            tokens = shlex.split(l)
            if len(tokens) >= 4 and tokens[0].upper() == "LINELOAD":
                lname = tokens[1]
                st_name = tokens[2]
                lc_id = ""
                direction = "GRAV"
                fval = 0.0
                for i in range(3, len(tokens)):
                    key = tokens[i].upper()
                    if key == "LC" and i + 1 < len(tokens): lc_id = tokens[i + 1]
                    elif key == "DIR" and i + 1 < len(tokens): direction = tokens[i + 1].upper()
                    elif key == "FVAL" and i + 1 < len(tokens): fval = float(tokens[i + 1])

                if (lname, st_name) in elem_lookup and lc_id in lc_map:
                    eid = elem_lookup[(lname, st_name)]
                    wz = -fval if direction == "GRAV" else (fval if direction == "Z" else 0.0)
                    wx = fval if direction == "X" else 0.0
                    wy = fval if direction == "Y" else 0.0
                    lc_map[lc_id].element_loads.append(
                        DistributedLoad(element_id=eid, wx=wx, wy=wy, wz=wz)
                    )

        # 14. Mass Source
        ms_lines = sections.get("MASS SOURCE", [])
        if ms_lines:
            ms_id = "MSSSRC1"
            inc_elem = True
            factors: List[MassSourceFactor] = []
            for l in ms_lines:
                tokens = shlex.split(l)
                if tokens and tokens[0].upper() == "MASSSOURCE":
                    ms_id = tokens[1]
                    for i in range(2, len(tokens) - 1):
                        if tokens[i].upper() == "INCLUDEELEMENTS":
                            inc_elem = tokens[i + 1].upper() == "YES"
                elif tokens and tokens[0].upper() == "MASSSOURCELOAD":
                    if len(tokens) >= 4:
                        factors.append(MassSourceFactor(load_case_id=tokens[2], factor=float(tokens[3])))
            model.mass_source = MassSource(
                id=ms_id,
                name="ETABS Mass Source",
                include_element_mass=inc_elem,
                factors=factors,
            )
            model.mass_sources = [model.mass_source]

        # 15. Load Cases (Modal & Response Spectrum)
        case_lines = sections.get("LOAD CASES", [])
        for l in case_lines:
            tokens = shlex.split(l)
            if len(tokens) >= 3 and tokens[0].upper() == "LOADCASE":
                cid = tokens[1]
                for i in range(2, len(tokens) - 1):
                    key = tokens[i].upper()
                    if key == "TYPE":
                        ctype = tokens[i + 1].upper()
                        if "MODAL" in ctype:
                            if not any(isinstance(c, ModalAnalysis) and c.id == cid for c in model.analysis_cases):
                                model.analysis_cases.append(ModalAnalysis(id=cid, num_modes=12))
                        elif "RESPONSE SPECTRUM" in ctype:
                            if not any(isinstance(c, ResponseSpectrumAnalysis) and c.id == cid for c in model.analysis_cases):
                                model.analysis_cases.append(
                                    ResponseSpectrumAnalysis(
                                        id=cid,
                                        direction="X",
                                        combination="CQC",
                                        damping_ratio=0.05,
                                        scale_factor=9.80665,
                                        function_name="asce7-22",
                                    )
                                )

        # Synthesize ELF lateral loads for native auto-seismic cases if point loads were omitted
        for lc in model.load_cases:
            if lc.auto_load and not lc.point_loads and getattr(model, "mass_source", None):
                try:
                    from struct_load import LoadGenerator, SeismicParameters
                    sparams = lc.seismic_params or {}
                    s_obj = SeismicParameters(
                        sds=float(sparams.get("sds", sparams.get("z", 1.0))),
                        sd1=float(sparams.get("sd1", 0.4)),
                        s1=float(sparams.get("s1", 0.35)),
                        tl=float(sparams.get("tl", 8.0)),
                        r_factor=float(sparams.get("r", sparams.get("r_factor", 8.0))),
                        importance_factor=float(sparams.get("i", sparams.get("importance_factor", 1.0))),
                        site_class=str(sparams.get("siteclass", sparams.get("site_class", sparams.get("s", "D")))),
                    )
                    dir_str = str(sparams.get("dir", "X")).upper()
                    direction = "Y" if "Y" in dir_str and "X" not in dir_str else "X"
                    gen = LoadGenerator(units=units_str)
                    gen.apply_lateral_seismic(
                        model,
                        params=s_obj,
                        direction=direction,
                        seismic_case_id=lc.id,
                        mass_source=model.mass_source,
                        distribution="uniform",
                    )
                except Exception:
                    pass

        return project
