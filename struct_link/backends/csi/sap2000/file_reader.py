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

from ....base import BaseModelReader
from ..s2k_parser import S2kParser, S2kTable


class Sap2000FileReader(BaseModelReader):
    """Imports a SAP2000 .s2k text file into a struct_core.StructuralModel."""

    def read_model(self, source: Union[str, Path]) -> StructuralModel:
        tables = S2kParser.parse_file(source)
        return self._build_model(tables)

    def read_project(self, source: Union[str, Path], title: Optional[str] = None) -> Project:
        tables = S2kParser.parse_file(source)
        model = self._build_model(tables)
        project = Project(model=model)
        if title:
            project.metadata.title = title
        elif hasattr(source, "stem"):
            project.metadata.title = source.stem
        else:
            project.metadata.title = Path(source).stem

        # Extract units from PROGRAM CONTROL if defined
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
                    import logging
                    logging.getLogger(__name__).warning(
                        "Could not parse SAP2000 CurrUnits '%s': %s", curr_units, exc
                    )

        return project

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
        # Table: "JOINT RESTRAINT ASSIGNMENTS" or "JOINT RESTRAINTS"
        restr_table = tables.get("JOINT RESTRAINT ASSIGNMENTS") or tables.get("JOINT RESTRAINTS")
        if restr_table:
            for row in restr_table.rows:
                jid = str(row.get("Joint", row.get("JointID", "")))
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
        # Table: "MATERIAL PROPERTIES 01 - GENERAL" (types, symmetry)
        mat_info: Dict[str, Dict[str, str]] = {}
        mat_gen_table = tables.get("MATERIAL PROPERTIES 01 - GENERAL")
        if mat_gen_table:
            for row in mat_gen_table.rows:
                mid = str(row.get("Material", ""))
                if mid:
                    mat_info[mid] = {
                        "type": str(row.get("Type", "")),
                        "sym_type": str(row.get("SymType", "")),
                    }

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
                unit_weight = None
                if "UnitWeight" in row and row["UnitWeight"] is not None:
                    try:
                        unit_weight = float(row["UnitWeight"])
                    except (ValueError, TypeError):
                        pass
                m_type = mat_info.get(mid, {}).get("type")
                materials.append(ElasticMaterial(id=mid, name=m_type, E=E, nu=nu, rho=rho, unit_weight=unit_weight))

        if not materials:
            if tables.get("FRAME SECTION PROPERTIES 01 - GENERAL") or tables.get("FRAME SECTIONS") or tables.get("CONNECTIVITY - FRAME"):
                raise ValueError("No materials defined in SAP2000 file.")

        # 4. Frame Sections
        # Table: "FRAME SECTION PROPERTIES 01 - GENERAL" or "FRAME SECTIONS"
        sec_mat_map: Dict[str, str] = {}
        sec_table = tables.get("FRAME SECTION PROPERTIES 01 - GENERAL") or tables.get("FRAME SECTIONS")
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
                J = float(row.get("J", 0.0))

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

        # Check for section assignments: "FRAME SECTION ASSIGNMENTS"
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
        valid_mat_ids = {m.id for m in materials}

        # Parse Local Axes Assignments
        # Table: "FRAME LOCAL AXES ASSIGNMENTS 1 - TYPICAL"
        frame_angle_map: Dict[str, float] = {}
        axes_table = tables.get("FRAME LOCAL AXES ASSIGNMENTS 1 - TYPICAL")
        if axes_table:
            for row in axes_table.rows:
                fid = str(row.get("Frame", ""))
                ang_val = row.get("Angle")
                if fid and ang_val is not None:
                    try:
                        frame_angle_map[fid] = float(ang_val)
                    except (ValueError, TypeError):
                        pass

        nodes_dict = {str(n.id): n for n in nodes}

        if conn_table:
            for row in conn_table.rows:
                fid = str(row.get("Frame", row.get("FrameID", "")))
                ji = str(row.get("JointI", ""))
                jj = str(row.get("JointJ", ""))
                if not fid or not ji or not jj:
                    continue
                sec_id = frame_sec_map.get(fid, default_sec_id)
                mat_id = sec_mat_map.get(sec_id, default_mat_id)
                if mat_id not in valid_mat_ids:
                    mat_id = default_mat_id

                # Calculate roll_angle from SAP2000 Angle
                sap_angle = frame_angle_map.get(fid, 0.0)
                ni = nodes_dict.get(ji)
                nj = nodes_dict.get(jj)
                if ni and nj:
                    dx = float(nj.x - ni.x)
                    dy = float(nj.y - ni.y)
                    dz = float(getattr(nj, "z", 0.0) - getattr(ni, "z", 0.0))
                    is_vertical = (dx**2 + dy**2) < 1e-12 and abs(dz) > 1e-12
                    if is_vertical:
                        roll_angle = sap_angle - 90.0
                    else:
                        roll_angle = sap_angle
                else:
                    roll_angle = sap_angle

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
        # Table: "LOAD PATTERN DEFINITIONS" or "LOAD CASE DEFINITIONS"
        pat_table = tables.get("LOAD PATTERN DEFINITIONS") or tables.get("LOAD CASE DEFINITIONS")
        if pat_table:
            for row in pat_table.rows:
                pat = str(row.get("LoadPat", row.get("Case", row.get("LoadCase", ""))))
                if pat and pat not in load_cases_dict:
                    self_wt = float(row.get("SelfWtMult", 0.0)) > 0.0
                    load_cases_dict[pat] = LoadCase(id=pat, include_self_weight=self_wt)

        # 7. Joint Loads -> LoadCases with PointLoads
        # Tables: "JOINT LOADS - FORCE", "AUTO SEISMIC LOADS TO JOINTS"
        for tbl_name in ("JOINT LOADS - FORCE", "AUTO SEISMIC LOADS TO JOINTS"):
            jt_loads_table = tables.get(tbl_name)
            if jt_loads_table:
                for row in jt_loads_table.rows:
                    jid = str(row.get("Joint", ""))
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

        # 8. Frame Loads -> DistributedLoads
        # Table: "FRAME LOADS - DISTRIBUTED"
        frm_loads_table = tables.get("FRAME LOADS - DISTRIBUTED")
        if frm_loads_table:
            for row in frm_loads_table.rows:
                fid = str(row.get("Frame", ""))
                pat = str(row.get("LoadPat", "DEAD"))
                if pat not in load_cases_dict:
                    load_cases_dict[pat] = LoadCase(id=pat)

                # Extract load magnitude at ends A and B (SAP2000 uses FOverLA/FOverLB)
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

                # Map direction to global or local components
                wx, wy, wz = 0.0, 0.0, 0.0
                w1, w2, w3 = 0.0, 0.0, 0.0
                is_local = False
                w_start: Optional[float] = None
                w_end: Optional[float] = None

                if "GRAV" in dir_name:
                    # SAP2000 Gravity acts downward along -Z
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
                        f"Unsupported load direction '{dir_name}' (coordinate system '{csys}') in SAP2000 file."
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
        # Table: "MASS SOURCE"
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
                    # In SAP2000, if Elements=No but DEAD (with SelfWtMult=1) is in mass source with multiplier > 0,
                    # DEAD represents element self-mass.
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
        # Table: "CASE - MODAL 1 - GENERAL"
        analysis_cases = []
        modal_table = tables.get("CASE - MODAL 1 - GENERAL")
        if modal_table:
            for row in modal_table.rows:
                cid = str(row.get("Case", "MODAL"))
                num_modes = int(row.get("MaxNumModes", 12))
                analysis_cases.append(ModalAnalysis(id=cid, num_modes=num_modes))

        # 11. Response Spectrum Analysis Cases
        # Tables: "CASE - RESPONSE SPECTRUM 1 - GENERAL", "CASE - RESPONSE SPECTRUM 2 - LOAD ASSIGNMENTS"
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
