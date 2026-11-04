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
    ConcreteMaterial,
    DistributedLoad,
    ElasticMaterial,
    ElementPointLoad,
    GeneralSection,
    LoadCase,
    LoadCaseFactor,
    LoadCombination,
    MassSource,
    MassSourceFactor,
    ModalAnalysis,
    Node,
    PointLoad,
    Project,
    RebarMaterial,
    RectangularSection,
    ResponseSpectrumAnalysis,
    StructuralModel,
    Support,
    Units,
    parse_units,
)

from ...base import BaseModelReader
from .s2k_parser import S2kParser, S2kTable

logger = logging.getLogger(__name__)


def _csi_default_mass_source() -> MassSource:
    """CSI default when a file defines no mass source: element self-mass only."""
    return MassSource(
        id="MSSSRC1",
        name="CSI default (element self-mass)",
        include_element_mass=True,
        factors=[],
    )


def _csi_material(
    material_id: str,
    csi_type: Optional[str],
    *,
    E: Optional[float],
    nu: Optional[float] = None,
    unit_weight: Optional[float] = None,
    rho: Optional[float] = None,
    fc: Optional[float] = None,
    fy: Optional[float] = None,
    fu: Optional[float] = None,
    messages: Optional[List[str]] = None,
) -> Any:
    """
    The struct_core material for a CSI material, with only the properties the file states (in its units): concrete
    with f'c -> ConcreteMaterial, rebar with Fy -> RebarMaterial, anything else (or concrete / rebar missing its
    strength) -> ElasticMaterial. Nothing is assumed; a property the file lacks is left out and noted in
    ``messages``.
    """
    if E is None:
        raise ValueError(f"CSI material {material_id!r} has no modulus of elasticity (E).")
    kind = (csi_type or "").strip().lower()
    common: Dict[str, Any] = {"id": material_id, "name": csi_type or None, "E": E}
    if nu is not None:
        common["nu"] = nu
    if unit_weight is not None:
        common["unit_weight"] = unit_weight
    if rho is not None:
        common["rho"] = rho
    if kind == "concrete" and fc is not None:
        return ConcreteMaterial(fc=fc, **common)
    if kind == "rebar" and fy is not None:
        return RebarMaterial(fy=fy, fu=fu, **common)
    if kind in ("concrete", "rebar") and messages is not None:
        missing = "f'c" if kind == "concrete" else "Fy"
        messages.append(f"Material {material_id!r} ({csi_type}) has no {missing}: imported as an elastic material; "
                        f"set its {missing} before a code design.")
    return ElasticMaterial(**common)


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

    def read_model(self, source: Union[str, Path], allow_partial_import: bool = False) -> StructuralModel:
        self.messages: List[str] = []  # import notes, e.g. a concrete material without f'c
        self.ignored_tables: List[str] = []
        self.dropped_items: List[str] = []
        path = Path(source)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        if self._is_dollar_e2k(content):
            proj = self._build_project_from_dollar_e2k(content, allow_partial_import=allow_partial_import)
            return proj.model

        tables = S2kParser.parse_string(content)
        return self._build_model(tables, allow_partial_import=allow_partial_import)

    def read_project(
        self,
        source: Union[str, Path],
        title: Optional[str] = None,
        allow_partial_import: bool = False,
    ) -> Project:
        self.messages: List[str] = []  # import notes, e.g. a concrete material without f'c
        self.ignored_tables: List[str] = []
        self.dropped_items: List[str] = []
        path = Path(source)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        file_title = title or (path.stem if hasattr(path, "stem") else str(path))

        if self._is_dollar_e2k(content):
            return self._build_project_from_dollar_e2k(
                content, title=file_title, allow_partial_import=allow_partial_import
            )

        tables = S2kParser.parse_string(content)
        model = self._build_model(tables, allow_partial_import=allow_partial_import)
        project = Project(model=model)
        project.metadata.title = file_title
        project.units = getattr(self, "_detected_units", Units())
        return project

    def _build_model(
        self, tables: Dict[str, S2kTable], allow_partial_import: bool = False
    ) -> StructuralModel:
        from struct_core import Units, parse_units

        prog_table = tables.get("PROGRAM CONTROL") or tables.get("PROGRAM INFORMATION")
        curr_units = None
        if prog_table and prog_table.rows:
            curr_units = prog_table.rows[0].get("CurrUnits")
        if not curr_units or not isinstance(curr_units, str) or not curr_units.strip():
            raise ValueError("The file states no units (PROGRAM CONTROL / CurrUnits)")
        try:
            model_units = parse_units(curr_units)
        except Exception as exc:
            raise ValueError(f"The file states no units (PROGRAM CONTROL / CurrUnits): {exc}")
        self._detected_units = model_units

        # Table classification: HANDLED, IGNORABLE, AFFECTS_RESULTS
        handled_tables = {
            "PROGRAM CONTROL",
            "PROGRAM INFORMATION",
            "PROJECT INFORMATION",
            "COORDINATE SYSTEMS",
            "ACTIVE DEGREES OF FREEDOM",
            "JOINT COORDINATES",
            "POINT COORDINATES",
            "JOINT RESTRAINT ASSIGNMENTS",
            "POINT ASSIGNMENTS - RESTRAINTS",
            "JOINT RESTRAINTS",
            "MATERIAL PROPERTIES 01 - GENERAL",
            "MATERIAL PROPERTIES - GENERAL",
            "MATERIAL PROPERTIES 03A - STEEL DATA",
            "MATERIAL PROPERTIES 03B - CONCRETE DATA",
            "MATERIAL PROPERTIES 03E - REBAR DATA",
            "MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES",
            "MATERIAL PROPERTIES",
            "FRAME SECTION PROPERTIES 02 - CONCRETE COLUMN",
            "FRAME SECTION PROPERTIES 03 - CONCRETE BEAM",
            "FRAME SECTION PROPERTIES 01 - GENERAL",
            "FRAME SECTION PROPERTIES",
            "FRAME SECTIONS",
            "FRAME SECTION ASSIGNMENTS",
            "CONNECTIVITY - FRAME",
            "LINE CONNECTIVITY",
            "FRAME LOCAL AXES ASSIGNMENTS 1 - TYPICAL",
            "FRAME LOCAL AXES ASSIGNMENTS",
            "LINE ASSIGNMENTS - LOCAL AXES",
            "FRAME RELEASE ASSIGNMENTS 1 - GENERAL",
            "FRAME RELEASE ASSIGNMENTS",
            "FRAME RELEASES",
            "FRAME PROPERTY MODIFIERS",
            "LINE PROPERTY MODIFIERS",
            "JOINT ADDED MASS ASSIGNMENTS",
            "POINT ADDED MASS ASSIGNMENTS",
            "LOAD PATTERN DEFINITIONS",
            "LOAD CASE DEFINITIONS",
            "AUTO SEISMIC - ASCE 7-22",
            "POINT LOADS - FORCE",
            "JOINT LOADS - FORCE",
            "AUTO SEISMIC LOADS TO JOINTS",
            "FRAME LOADS - DISTRIBUTED",
            "LINE LOADS - DISTRIBUTED",
            "FRAME LOADS - POINT",
            "LINE LOADS - POINT",
            "COMBINATION DEFINITIONS",
            "LOAD COMBINATION DEFINITIONS",
            "MASS SOURCE",
            "CASE - MODAL 1 - GENERAL",
            "CASE - RESPONSE SPECTRUM 1 - GENERAL",
            "CASE - RESPONSE SPECTRUM 2 - LOAD ASSIGNMENTS",
        }

        ignorable_keywords = (
            "DATABASE FORMAT",
            "PREFERENCES",
            "OPTIONS - COLORS",
            "COLORS",
            "GRID LINES",
            "GROUPS",
            "OBJECT DESIGN PROCEDURES",
            "OUTPUT OPTIONS",
            "CASE - STATIC",
            "ANALYSIS OPTIONS",
            "DISPLAY",
            "PRINT",
            "COMMENT",
            "USER COMMENTS",
            "HYPERLINK",
            "MATERIAL LIST",
            "FRAME OUTPUT STATION ASSIGNMENTS",
            "FUNCTION - PLOT FUNCTIONS",
            "REBAR SIZES",
            "FRAME AUTO MESH",
            "FRAME LOAD TRANSFER",
            "FRAME DESIGN PROCEDURES",
            "OVERWRITES - CONCRETE DESIGN",
            "OVERWRITES - STEEL DESIGN",
            "SOLID PROPERTY DEFINITIONS",
            "FUNCTION - RESPONSE SPECTRUM",
            "FUNCTION - TIME HISTORY",
            "FUNCTION - POWER SPECTRAL",
            "FUNCTION - STEADY STATE",
            "AUTO WAVE",
            "JOINT PATTERN DEFINITIONS",
            "MATERIAL PROPERTIES 03F",
            "MATERIAL PROPERTIES 03J",
            "MATERIAL PROPERTIES 03K",
            "MATERIAL PROPERTIES 06",
            "MATERIAL PROPERTIES 09",
            "FRAME SECTION PROPERTIES 13",
        )

        unsupported_structural_keywords = (
            "SPRING",
            "OFFSET",
            "CONSTRAINT",
            "DIAPHRAGM",
            "CONNECTIVITY - AREA",
            "AREA SECTION ASSIGNMENTS",
            "AREA ASSIGNMENTS",
            "AREA LOADS",
            "LINE ASSIGNMENTS - AREA",
            "CONNECTIVITY - SOLID",
            "SOLID SECTION ASSIGNMENTS",
            "CONNECTIVITY - LINK",
            "LINK ASSIGNMENTS",
            "CONNECTIVITY - TENDON",
            "TENDON ASSIGNMENTS",
            "CONNECTIVITY - CABLE",
            "CABLE ASSIGNMENTS",
            "HINGE",
            "PANEL ZONE",
        )

        for tname, tbl in tables.items():
            t_up = tname.upper()
            if t_up in handled_tables:
                continue
            is_ignorable = any(kw in t_up for kw in ignorable_keywords)
            if is_ignorable:
                self.ignored_tables.append(tname)
                continue

            # Affects results or unhandled table
            is_unsupported = any(kw in t_up for kw in unsupported_structural_keywords)
            if tbl.rows:
                if is_unsupported:
                    if not allow_partial_import:
                        raise ValueError(
                            f"File contains unsupported table {tname!r} ({len(tbl.rows)} rows). "
                            f"Set allow_partial_import=True to proceed with partial model."
                        )
                    else:
                        self.messages.append(f"TABLE {tname} ({len(tbl.rows)} rows) was not imported")
                        self.dropped_items.append(tname)
                else:
                    self.messages.append(f"TABLE {tname} ({len(tbl.rows)} rows) was not imported")
                    self.dropped_items.append(tname)

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

        # 1b. Joint Added Mass Assignments
        mass_table = tables.get("JOINT ADDED MASS ASSIGNMENTS") or tables.get("POINT ADDED MASS ASSIGNMENTS")
        if mass_table:
            node_map = {str(n.id): n for n in nodes}
            for row in mass_table.rows:
                jid = str(row.get("Joint", row.get("Point", "")))
                if jid in node_map:
                    n = node_map[jid]
                    u1 = float(row.get("Mass1", row.get("U1", row.get("MassX", 0.0))) or 0.0)
                    u2 = float(row.get("Mass2", row.get("U2", row.get("MassY", 0.0))) or 0.0)
                    u3 = float(row.get("Mass3", row.get("U3", row.get("MassZ", 0.0))) or 0.0)
                    if abs(u1 - u2) > 1e-6 or abs(u2 - u3) > 1e-6:
                        if not allow_partial_import:
                            raise ValueError(
                                f"Joint {jid!r} has non-isotropic directional added mass (U1={u1}, U2={u2}, U3={u3}). "
                                f"Set allow_partial_import=True to average directional masses."
                            )
                        else:
                            avg_mass = (u1 + u2 + u3) / 3.0
                            n.mass = (getattr(n, "mass", 0.0) or 0.0) + avg_mass
                            self.messages.append(f"Joint {jid!r} non-isotropic mass averaged to {avg_mass:g}")
                    else:
                        n.mass = (getattr(n, "mass", 0.0) or 0.0) + u1

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

        def _float(row: Dict[str, Any], *keys: str) -> Optional[float]:
            for key in keys:
                if row.get(key) is not None:
                    try:
                        return float(row[key])
                    except (ValueError, TypeError):
                        return None
            return None

        # Design strengths: concrete f'c (03B), rebar Fy / Fu (03E), and steel Fy / Fu (03A)
        strengths: Dict[str, Dict[str, Optional[float]]] = {}
        for name in (
            "MATERIAL PROPERTIES 03A - STEEL DATA",
            "MATERIAL PROPERTIES 03B - CONCRETE DATA",
            "MATERIAL PROPERTIES 03E - REBAR DATA",
        ):
            table = tables.get(name)
            for row in (table.rows if table else []):
                mid = str(row.get("Material", ""))
                if mid:
                    strengths[mid] = {"fc": _float(row, "Fc"), "fy": _float(row, "Fy"), "fu": _float(row, "Fu")}

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
                strength = strengths.get(mid, {})
                raw_mass = _float(row, "UnitMass", "MassDensity")
                rho_val = (raw_mass / model_units.consistent_mass_factor) if raw_mass is not None else None
                materials.append(_csi_material(
                    mid, mat_info.get(mid, {}).get("type"),
                    E=_float(row, "E1", "E"), nu=_float(row, "U12", "Nu"),
                    unit_weight=_float(row, "UnitWeight"), rho=rho_val,
                    fc=strength.get("fc"), fy=strength.get("fy"), fu=strength.get("fu"), messages=self.messages,
                ))

        # 4. Frame Sections (each with its material) and the reinforcement of concrete sections: bar materials, and the
        # clear cover to the ties of columns (SAP2000 "Cover"). A beam's TopCover / BotCover is measured to the bar
        # centroid, not clear cover, so beams keep no cover (set it before a code design).
        concrete_sections: Dict[str, Dict[str, Any]] = {}
        for name, is_column in (("FRAME SECTION PROPERTIES 02 - CONCRETE COLUMN", True),
                                ("FRAME SECTION PROPERTIES 03 - CONCRETE BEAM", False)):
            table = tables.get(name)
            for row in (table.rows if table else []):
                sname = str(row.get("SectionName", ""))
                if not sname:
                    continue
                entry: Dict[str, Any] = {}
                if row.get("RebarMatL"):
                    entry["rebar_material_id"] = str(row["RebarMatL"])
                if row.get("RebarMatC"):
                    entry["transverse_rebar_material_id"] = str(row["RebarMatC"])
                cover = _float(row, "Cover") if is_column else None
                if cover is not None and cover > 0.0:
                    entry["cover"] = cover
                concrete_sections[sname] = entry

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

                shape = str(row.get("Shape", "")).upper()
                t3 = float(row.get("t3", row.get("Depth", 0.0)))
                t2 = float(row.get("t2", row.get("Width", 0.0)))
                A = float(row.get("Area", row.get("A", 0.0)))
                I33 = float(row.get("I33", row.get("Iz", 0.0)))
                I22 = float(row.get("I22", row.get("Iy", 0.0)))
                J = float(row.get("J", row.get("TorsConst", 0.0)))

                as2 = float(row.get("AS2", row.get("As2", 0.0)))
                as3 = float(row.get("AS3", row.get("As3", 0.0)))

                if not mat_name:
                    raise ValueError(f"CSI frame section {sname!r} names no material.")
                reinforcement = concrete_sections.get(sname, {})
                if shape == "RECTANGULAR" or (t3 > 0 and t2 > 0 and shape in ("", "CONCRETE", "RECT")):
                    sections.append(RectangularSection(id=sname, material_id=mat_name, b=t2, h=t3, **reinforcement))
                elif shape == "CIRCULAR" or (t3 > 0 and shape == "CIRCLE"):
                    sections.append(CircularSection(id=sname, material_id=mat_name, d=t3, **reinforcement))
                else:
                    sections.append(
                        GeneralSection(
                            id=sname,
                            material_id=mat_name,
                            A=A if A > 0 else 1.0,
                            I3=I33 if I33 > 0 else 1.0,
                            I2=I22 if I22 > 0 else 1.0,
                            J=J if J > 0 else 1.0,
                            As2=as2 if as2 > 0 else None,
                            As3=as3 if as3 > 0 else None,
                        )
                    )

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

        # 4b. Frame Property Modifiers
        mod_table = tables.get("FRAME PROPERTY MODIFIERS") or tables.get("LINE PROPERTY MODIFIERS")
        prop_mods: Dict[str, Dict[str, float]] = {}
        if mod_table:
            for row in mod_table.rows:
                fid = str(row.get("Frame", row.get("Line", "")))
                mods = {}
                col_map = [
                    (("A", "AMod"), "A"),
                    (("A2", "As2", "A2Mod", "As2Mod"), "As2"),
                    (("A3", "As3", "A3Mod", "As3Mod"), "As3"),
                    (("J", "JMod"), "J"),
                    (("I2", "I2Mod"), "I2"),
                    (("I3", "I3Mod"), "I3"),
                    (("Mass", "MassMod", "mass"), "mass"),
                    (("Weight", "WeightMod", "weight"), "weight"),
                ]
                for cols, key in col_map:
                    for col in cols:
                        if col in row and row[col] is not None:
                            try:
                                v = float(row[col])
                                if abs(v - 1.0) > 1e-6:
                                    mods[key] = v
                                break
                            except (ValueError, TypeError):
                                pass
                if mods:
                    prop_mods[fid] = mods

        # Parse Frame Releases (member-local P, V2, V3, T, M2, M3)
        frame_releases_map: Dict[str, Tuple[List[bool], List[bool]]] = {}
        rel_table = (
            tables.get("FRAME RELEASE ASSIGNMENTS 1 - GENERAL")
            or tables.get("FRAME RELEASE ASSIGNMENTS")
            or tables.get("FRAME RELEASES")
        )
        if rel_table:
            for row in rel_table.rows:
                fid = str(row.get("Frame", row.get("Line", "")))
                if fid:
                    def _is_rel(val):
                        if isinstance(val, bool):
                            return val
                        return str(val or "").strip().lower() in ("yes", "true", "1", "y")

                    ri = [
                        _is_rel(row.get("PI")),
                        _is_rel(row.get("V2I")),
                        _is_rel(row.get("V3I")),
                        _is_rel(row.get("TI")),
                        _is_rel(row.get("M2I")),
                        _is_rel(row.get("M3I")),
                    ]
                    rj = [
                        _is_rel(row.get("PJ")),
                        _is_rel(row.get("V2J")),
                        _is_rel(row.get("V3J")),
                        _is_rel(row.get("TJ")),
                        _is_rel(row.get("M2J")),
                        _is_rel(row.get("M3J")),
                    ]
                    frame_releases_map[fid] = (ri, rj)

        nodes_dict = {str(n.id): n for n in nodes}

        if conn_table:
            for row in conn_table.rows:
                fid = str(row.get("Frame", row.get("Line", row.get("FrameID", row.get("LineID", "")))))
                ji = str(row.get("JointI", row.get("PointI", "")))
                jj = str(row.get("JointJ", row.get("PointJ", "")))
                if not fid or not ji or not jj:
                    continue
                sec_id = row.get("SectionName") or frame_sec_map.get(fid)
                if sec_id is None:
                    raise ValueError(f"CSI frame {fid!r} has no section assignment.")

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

                rel_i, rel_j = frame_releases_map.get(fid, ([False] * 6, [False] * 6))
                elements.append(
                    BeamElement(
                        id=fid,
                        start_node=ji,
                        end_node=jj,
                        section_id=sec_id,
                        # CSI frames are Timoshenko members (shear areas AS2/AS3), as in the ETABS reader
                        include_shear_deformation=True,
                        roll_angle=roll_angle,
                        release_i=rel_i,
                        release_j=rel_j,
                        stiffness_modifiers=prop_mods.get(fid),
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
                    if not jid:
                        continue
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

                # Span positions
                rel_a = float(row.get("RelDistA", row.get("DistA", 0.0)) or 0.0)
                rel_b = float(row.get("RelDistB", row.get("DistB", 1.0)) or 1.0)
                if rel_a > rel_b:
                    rel_a, rel_b = rel_b, rel_a
                if abs(rel_a - rel_b) < 1e-6:
                    rel_a = 0.0
                    rel_b = 1.0

                load_kwargs = {
                    "element_id": fid,
                    "wx": wx,
                    "wy": wy,
                    "wz": wz,
                    "start": rel_a,
                    "end": rel_b,
                    "is_local": is_local,
                }
                if is_local:
                    load_kwargs["w1"] = w1
                    load_kwargs["w2"] = w2
                    load_kwargs["w3"] = w3
                    if val_a != val_b:
                        if abs(w1) > 1e-6: load_kwargs["w1_end"] = w_end
                        if abs(w2) > 1e-6: load_kwargs["w2_end"] = w_end
                        if abs(w3) > 1e-6: load_kwargs["w3_end"] = w_end
                else:
                    if val_a != val_b:
                        if abs(wx) > 1e-6: load_kwargs["wx_end"] = w_end
                        if abs(wy) > 1e-6: load_kwargs["wy_end"] = w_end
                        if abs(wz) > 1e-6: load_kwargs["wz_end"] = w_end

                load_cases_dict[pat].element_loads.append(
                    DistributedLoad(**load_kwargs)
                )

        # 8b. Frame / Line Point Loads -> ElementPointLoad
        frm_pt_table = tables.get("FRAME LOADS - POINT") or tables.get("LINE LOADS - POINT")
        if frm_pt_table:
            for row in frm_pt_table.rows:
                fid = str(row.get("Frame", row.get("Line", "")))
                pat = str(row.get("LoadPat", "DEAD"))
                if pat not in load_cases_dict:
                    load_cases_dict[pat] = LoadCase(id=pat)

                csys = str(row.get("CoordSys", "GLOBAL")).strip().upper()
                ltype = str(row.get("Type", "Force")).strip().upper()
                dir_name = str(row.get("Dir", "Gravity")).strip().upper()
                pos = float(row.get("RelDist", row.get("Dist", 0.0)) or 0.0)
                val = float(row.get("PointLoad", row.get("Force", row.get("Val", 0.0))) or 0.0)

                is_loc = (csys == "LOCAL")
                fx = fy = fz = mx = my = mz = 0.0

                if ltype == "MOMENT":
                    if dir_name in ("1", "+1") or (is_loc and dir_name in ("X", "+X")):
                        mx = val; is_loc = True
                    elif dir_name in ("-1",) or (is_loc and dir_name in ("-X",)):
                        mx = -val; is_loc = True
                    elif dir_name in ("2", "+2") or (is_loc and dir_name in ("Y", "+Y")):
                        my = val; is_loc = True
                    elif dir_name in ("-2",) or (is_loc and dir_name in ("-Y",)):
                        my = -val; is_loc = True
                    elif dir_name in ("3", "+3") or (is_loc and dir_name in ("Z", "+Z")):
                        mz = val; is_loc = True
                    elif dir_name in ("-3",) or (is_loc and dir_name in ("-Z",)):
                        mz = -val; is_loc = True
                    elif dir_name in ("Z", "+Z"): mz = val
                    elif dir_name in ("-Z",): mz = -val
                    elif dir_name in ("Y", "+Y"): my = val
                    elif dir_name in ("-Y",): my = -val
                    elif dir_name in ("X", "+X"): mx = val
                    elif dir_name in ("-X",): mx = -val
                else:  # Force
                    if "GRAV" in dir_name: fz = -abs(val)
                    elif dir_name in ("Z", "+Z"): fz = val
                    elif dir_name in ("-Z",): fz = -val
                    elif dir_name in ("Y", "+Y"): fy = val
                    elif dir_name in ("-Y",): fy = -val
                    elif dir_name in ("X", "+X"): fx = val
                    elif dir_name in ("-X",): fx = -val
                    elif dir_name in ("1", "+1") or (is_loc and dir_name in ("X", "+X")):
                        fx = val; is_loc = True
                    elif dir_name in ("-1",) or (is_loc and dir_name in ("-X",)):
                        fx = -val; is_loc = True
                    elif dir_name in ("2", "+2") or (is_loc and dir_name in ("Y", "+Y")):
                        fy = val; is_loc = True
                    elif dir_name in ("-2",) or (is_loc and dir_name in ("-Y",)):
                        fy = -val; is_loc = True
                    elif dir_name in ("3", "+3") or (is_loc and dir_name in ("Z", "+Z")):
                        fz = val; is_loc = True
                    elif dir_name in ("-3",) or (is_loc and dir_name in ("-Z",)):
                        fz = -val; is_loc = True

                load_cases_dict[pat].element_point_loads.append(
                    ElementPointLoad(
                        element_id=fid,
                        position=pos,
                        fx=fx, fy=fy, fz=fz,
                        mx=mx, my=my, mz=mz,
                        is_local=is_loc,
                    )
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
        else:
            mass_source = _csi_default_mass_source()

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

        # 11. Combinations
        combo_table = tables.get("COMBINATION DEFINITIONS") or tables.get("LOAD COMBINATION DEFINITIONS")
        combinations_list: List[LoadCombination] = []
        if combo_table:
            comb_dict: Dict[str, LoadCombination] = {}
            for row in combo_table.rows:
                cname = str(row.get("ComboName", row.get("CombinationName", ""))).strip()
                case_name = str(row.get("CaseName", row.get("LoadName", ""))).strip()
                sf = float(row.get("ScaleFactor", row.get("Factor", row.get("SF", 1.0))) or 1.0)
                ctype = str(row.get("ComboType", row.get("DesignType", ""))).upper()
                kind = "service" if "SERVICE" in ctype else "strength"
                if not cname or not case_name:
                    continue
                if cname not in comb_dict:
                    comb_dict[cname] = LoadCombination(
                        id=cname,
                        name=cname,
                        kind=kind,
                        method="ASD" if kind == "service" else "LRFD",
                        factors=[LoadCaseFactor(load_case_id=case_name, factor=sf)],
                    )
                else:
                    comb_dict[cname].factors.append(LoadCaseFactor(load_case_id=case_name, factor=sf))
            combinations_list = list(comb_dict.values())

        return StructuralModel(
            nodes=nodes,
            elements=elements,
            materials=materials,
            sections=sections,
            supports=supports,
            load_cases=list(load_cases_dict.values()),
            load_combinations=combinations_list,
            analysis_cases=analysis_cases,
            mass_source=mass_source,
            mass_sources=[mass_source] if mass_source else [],
        )

    def _build_project_from_dollar_e2k(
        self,
        content: str,
        title: Optional[str] = None,
        allow_partial_import: bool = False,
    ) -> Project:
        """Parse native ETABS text data ($ SECTION command syntax) into a Project."""
        sections: Dict[str, List[str]] = {}
        current_sec: Optional[str] = None
        elem_id_map: Dict[Tuple[str, str], str] = {}
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("; ELEM_MAP"):
                parts = stripped.split()
                if len(parts) >= 4:
                    elem_id_map[(parts[2], parts[3])] = parts[1]
            elif stripped.startswith("$"):
                current_sec = stripped[1:].strip().upper()
                sections[current_sec] = []
            elif current_sec and stripped:
                sections[current_sec].append(stripped)

        # Check for unhandled or unsupported sections (F1 / F6)
        handled_dollar_sections = {
            "PROGRAM",
            "CONTROLS",
            "STORIES - IN SEQUENCE FROM TOP",
            "MATERIAL PROPERTIES",
            "CONCRETE SECTIONS",
            "FRAME SECTIONS",
            "POINT COORDINATES",
            "POINT ASSIGNS",
            "LINE CONNECTIVITIES",
            "LINE ASSIGNS",
            "LOAD PATTERNS",
            "POINT OBJECT LOADS",
            "FRAME OBJECT LOADS",
            "MASS SOURCE",
            "LOAD CASES",
        }
        for sec_name, sec_lines in sections.items():
            s_up = sec_name.upper()
            is_unsupported = any(
                kw in s_up for kw in ("SPRING", "OFFSET", "CONSTRAINT", "DIAPHRAGM", "AREA")
            )
            if is_unsupported and sec_lines:
                if not allow_partial_import:
                    raise ValueError(
                        f"File contains unsupported section ${sec_name} ({len(sec_lines)} lines). "
                        f"Set allow_partial_import=True to import the supported model elements anyway."
                    )
                else:
                    self.messages.append(f"SECTION ${sec_name} ({len(sec_lines)} lines) was not imported")
            elif s_up not in handled_dollar_sections:
                self.messages.append(f"SECTION ${sec_name} ({len(sec_lines)} lines) was not imported")

        model = StructuralModel()
        project = Project(model=model)

        # 1. Controls / Units
        units_str = None
        ctrl_lines = sections.get("CONTROLS", [])
        for l in ctrl_lines:
            tokens = shlex.split(l)
            if tokens and tokens[0].upper() == "UNITS":
                f_u = tokens[1].upper() if len(tokens) > 1 else "KN"
                l_u = tokens[2].upper() if len(tokens) > 2 else "M"
                units_str = f"{f_u.lower()}, {l_u.lower()}"
            if tokens and tokens[0].upper() == "TITLE1" and not title:
                title = tokens[1]

        if not units_str:
            raise ValueError("The file states no units (CONTROLS / UNITS)")

        if title:
            project.metadata.title = title
        try:
            from struct_core import parse_units
            parsed_units = parse_units(units_str)
            project.units = parsed_units
        except Exception as exc:
            raise ValueError(f"The file states no units (CONTROLS / UNITS): {exc}")

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

        # 3. Materials: only the properties the file states (in its units); no mass density is derived, the weight
        # per volume carries the self-weight
        mat_lines = sections.get("MATERIAL PROPERTIES", [])
        mat_dict: Dict[str, Dict[str, Any]] = {}
        numeric = {"WEIGHTPERVOLUME": "unit_weight", "E": "E", "U": "nu", "FC": "fc", "FY": "fy", "FU": "fu"}
        for l in mat_lines:
            tokens = shlex.split(l)
            if len(tokens) >= 2 and tokens[0].upper() == "MATERIAL":
                mid = tokens[1]
                entry = mat_dict.setdefault(mid, {})
                for i in range(2, len(tokens) - 1):
                    key = tokens[i].upper()
                    if key == "TYPE":
                        entry["type"] = tokens[i + 1]
                    elif key in numeric and numeric[key] not in entry:
                        try:
                            entry[numeric[key]] = float(tokens[i + 1])
                        except ValueError:
                            pass

        for mid, mdata in mat_dict.items():
            model.materials.append(_csi_material(
                mid, mdata.get("type"), E=mdata.get("E"), nu=mdata.get("nu"), unit_weight=mdata.get("unit_weight"),
                fc=mdata.get("fc"), fy=mdata.get("fy"), fu=mdata.get("fu"), messages=self.messages,
            ))

        # Reinforcement of concrete sections: bar materials, and the clear cover to the confinement bars of columns
        # (ETABS COVER). A beam's COVERTOP / COVERBOTTOM is measured to the bar centroid, not clear cover, so beams
        # keep no cover (set it before a code design).
        concrete_sections: Dict[str, Dict[str, Any]] = {}
        for l in sections.get("CONCRETE SECTIONS", []):
            tokens = shlex.split(l)
            if len(tokens) >= 2 and tokens[0].upper() == "CONCRETESECTION":
                values = {tokens[i].upper(): tokens[i + 1] for i in range(2, len(tokens) - 1)}
                entry: Dict[str, Any] = {}
                if "LONGBARMATERIAL" in values:
                    entry["rebar_material_id"] = values["LONGBARMATERIAL"]
                if "CONFINEBARMATERIAL" in values:
                    entry["transverse_rebar_material_id"] = values["CONFINEBARMATERIAL"]
                if values.get("TYPE", "").upper() == "COLUMN" and "COVER" in values:
                    entry["cover"] = float(values["COVER"])
                concrete_sections[tokens[1]] = entry

        # 4. Frame Sections
        sec_lines = sections.get("FRAME SECTIONS", [])
        for l in sec_lines:
            tokens = shlex.split(l)
            if len(tokens) >= 2 and tokens[0].upper() == "FRAMESECTION":
                sec_id = tokens[1]
                values = {tokens[i].upper(): tokens[i + 1] for i in range(2, len(tokens) - 1)}
                shape = values.get("SHAPE", "").upper()
                material_id = values.get("MATERIAL")
                if material_id is None:
                    raise ValueError(f"ETABS frame section {sec_id!r} names no material.")
                reinforcement = concrete_sections.get(sec_id, {})
                if "RECT" in shape:
                    model.sections.append(RectangularSection(
                        id=sec_id, material_id=material_id, b=float(values["B"]), h=float(values["D"]), **reinforcement))
                elif "CIRC" in shape:
                    model.sections.append(CircularSection(
                        id=sec_id, material_id=material_id, d=float(values["D"]), **reinforcement))
                else:
                    self.messages.append(f"Frame section {sec_id!r} ({values.get('SHAPE')}) is not imported: only "
                                         f"rectangular and circular shapes are supported.")

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
        line_assigns: List[Tuple[str, str, str, float, List[bool], List[bool]]] = []
        for l in assign_lines:
            tokens = shlex.split(l)
            if len(tokens) >= 3 and tokens[0].upper() == "LINEASSIGN":
                lname = tokens[1]
                st_name = tokens[2]
                sec_id = ""
                ang = 0.0
                rel_i = [False] * 6
                rel_j = [False] * 6
                for i in range(3, len(tokens)):
                    key = tokens[i].upper()
                    if key == "SECTION" and i + 1 < len(tokens):
                        sec_id = tokens[i + 1]
                    elif key == "ANG" and i + 1 < len(tokens):
                        ang = float(tokens[i + 1])
                    elif key == "RELEASE" and i + 1 < len(tokens):
                        rel_toks = set(tokens[i + 1].upper().split())
                        rel_i = [
                            "PI" in rel_toks,
                            "V2I" in rel_toks,
                            "V3I" in rel_toks,
                            "TI" in rel_toks,
                            "M2I" in rel_toks,
                            "M3I" in rel_toks,
                        ]
                        rel_j = [
                            "PJ" in rel_toks,
                            "V2J" in rel_toks,
                            "V3J" in rel_toks,
                            "TJ" in rel_toks,
                            "M2J" in rel_toks,
                            "M3J" in rel_toks,
                        ]
                line_assigns.append((lname, st_name, sec_id, ang, rel_i, rel_j))

        # 9. Create 3D Nodes
        needed_nodes = set(restr_map.keys())
        for (lname, st_name, _, _, _, _) in line_assigns:
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
        for lname, st_name, sec_id, ang, rel_i, rel_j in line_assigns:
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
                orig_eid = elem_id_map.get((lname, st_name), elem_counter)
                elem = BeamElement(
                    id=orig_eid,
                    start_node=n_start,
                    end_node=n_end,
                    section_id=sec_id,
                    include_shear_deformation=True,
                    roll_angle=eff_ang,
                    release_i=rel_i,
                    release_j=rel_j,
                )
                model.elements.append(elem)
                elem_lookup[(lname, st_name)] = elem_counter
                elem_counter += 1
            elif ltype == "BEAM":
                if (pti, st_name) not in node_id_map or (ptj, st_name) not in node_id_map:
                    continue
                n_start = node_id_map[(pti, st_name)]
                n_end = node_id_map[(ptj, st_name)]
                orig_eid = elem_id_map.get((lname, st_name), elem_counter)
                elem = BeamElement(
                    id=orig_eid,
                    start_node=n_start,
                    end_node=n_end,
                    section_id=sec_id,
                    include_shear_deformation=True,
                    roll_angle=ang,
                    release_i=rel_i,
                    release_j=rel_j,
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
        else:
            model.mass_source = _csi_default_mass_source()
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
                        sds=float(sparams.get("sds") or sparams.get("z") or 1.0),
                        sd1=float(sparams.get("sd1") or 0.4),
                        s1=float(sparams.get("s1") or 0.35),
                        tl=float(sparams.get("tl") or 8.0),
                        r_factor=float(sparams.get("r") or sparams.get("r_factor") or 8.0),
                        importance_factor=float(sparams.get("i") or sparams.get("importance_factor") or 1.0),
                        site_class=str(sparams.get("siteclass", sparams.get("site_class", sparams.get("s", "D")))),
                        risk_category=str(sparams.get("risk_category", "II")),
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
