"""
ETABS .e2k file writer.

Exports a struct_core.StructuralModel or Project into a native ETABS text file (.e2k).
Matches the native CSI ETABS dollar command syntax ($ SECTION) required by ETABS 23.3.2+.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from struct_core import (
    CircularSection,
    ModalAnalysis,
    Project,
    RectangularSection,
    ResponseSpectrumAnalysis,
    StructuralModel,
)

from ....base import BaseModelWriter
from ..materials import _CSI_MATERIAL_TYPES, _concrete_sections


class EtabsFileWriter(BaseModelWriter):
    """Exports struct_core.StructuralModel or Project into native ETABS .e2k text file."""

    def write_model(
        self,
        model_or_project: Union[StructuralModel, Project],
        target_path: Union[str, Path],
        allow_partial_export: bool = False,
    ) -> Path:
        model = (
            model_or_project.model
            if isinstance(model_or_project, Project)
            else model_or_project
        )
        if model is None:
            raise ValueError("Provided project contains no structural model.")

        spring_nodes = [
            str(s.node_id)
            for s in getattr(model, "supports", [])
            if any(getattr(s, k, 0.0) for k in ("kx", "ky", "kz", "krx", "kry", "krz"))
        ]
        if spring_nodes and not allow_partial_export:
            raise ValueError(
                f"Model contains support springs on nodes {spring_nodes}, which are not supported for export. "
                f"Use allow_partial_export=True to export without springs."
            )
        self.dropped_springs = spring_nodes if allow_partial_export else []
        self.dropped_items = [f"Support springs on nodes {', '.join(self.dropped_springs)}"] if self.dropped_springs else []

        path = Path(target_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Determine Units
        units_force = "KN"
        units_len = "M"
        units_temp = "C"

        units_obj = None
        if isinstance(model_or_project, Project) and model_or_project.metadata and model_or_project.metadata.units:
            units_obj = model_or_project.metadata.units
        elif getattr(model, "units", None):
            try:
                from struct_core import parse_units
                u_val = getattr(model, "units", None)
                units_obj = parse_units(u_val) if isinstance(u_val, str) else u_val
            except Exception:
                pass

        if units_obj:
            f = (units_obj.force or "").upper()
            l = (units_obj.length or "").upper()
            if "KIP" in f and "IN" in l:
                units_force, units_len, units_temp = "KIP", "IN", "F"
            elif "KIP" in f and "FT" in l:
                units_force, units_len, units_temp = "KIP", "FT", "F"
            elif "LB" in f and "IN" in l:
                units_force, units_len, units_temp = "LB", "IN", "F"
            elif "LB" in f and "FT" in l:
                units_force, units_len, units_temp = "LB", "FT", "F"
            elif "N" in f and "MM" in l:
                units_force, units_len, units_temp = "N", "MM", "C"
            elif "KN" in f and "MM" in l:
                units_force, units_len, units_temp = "KN", "MM", "C"
            elif "KN" in f and "M" in l:
                units_force, units_len, units_temp = "KN", "M", "C"
            elif "N" in f and "M" in l:
                units_force, units_len, units_temp = "N", "M", "C"

        now_str = datetime.now().strftime("%m/%d/%Y %I:%M:%S %p")
        lines: List[str] = []

        # ---------------------------------------------------------------------
        # 1. Header & Program Information
        # ---------------------------------------------------------------------
        lines.append(f"$ File {path.name} saved {now_str}")
        lines.append("")
        lines.append("$ PROGRAM INFORMATION")
        lines.append('  PROGRAM  "ETABS"  VERSION "23.3.2"  ')
        lines.append("")

        # ---------------------------------------------------------------------
        # 2. Controls
        # ---------------------------------------------------------------------
        lines.append("$ CONTROLS")
        lines.append(f'  UNITS  "{units_force}"  "{units_len}"  "{units_temp}"  ')
        title = ""
        if isinstance(model_or_project, Project) and model_or_project.metadata and model_or_project.metadata.title:
            title = model_or_project.metadata.title
        if title:
            lines.append(f'  TITLE1  "{title}"  ')
        lines.append("  PREFERENCE  MERGETOL 0.001")
        lines.append("")

        # ---------------------------------------------------------------------
        # 3. Stories
        # ---------------------------------------------------------------------
        z_coords = sorted(list(set(round(n.z, 6) for n in model.nodes))) if model.nodes else [0.0]
        z0 = z_coords[0]
        stories: List[Tuple[str, float, float]] = []
        if len(z_coords) > 1:
            for k in range(1, len(z_coords)):
                h = z_coords[k] - z_coords[k - 1]
                stories.append((f"Story{k}", h, z_coords[k]))
        else:
            stories.append(("Story1", 3.0, z0 + 3.0))

        lines.append("$ STORIES - IN SEQUENCE FROM TOP")
        for k in range(len(stories) - 1, -1, -1):
            st_name, st_h, _ = stories[k]
            if k == 0:
                lines.append(f'  STORY "{st_name}"  HEIGHT {st_h:g} MASTERSTORY "Yes"  ')
            else:
                lines.append(f'  STORY "{st_name}"  HEIGHT {st_h:g} SIMILARTO "Story1"  ')
        lines.append(f'  STORY "Base"  ELEV {z0:g} ')
        lines.append("")

        # ---------------------------------------------------------------------
        # 4. Grids
        # ---------------------------------------------------------------------
        x_coords = sorted(list(set(round(n.x, 6) for n in model.nodes))) if model.nodes else [0.0]
        y_coords = sorted(list(set(round(n.y, 6) for n in model.nodes))) if model.nodes else [0.0]
        lines.append("$ GRIDS")
        lines.append("  GRIDSYSTEM \"G1\"  TYPE \"CARTESIAN\"  BUBBLESIZE 1.25 ")
        for i, x in enumerate(x_coords):
            label = chr(ord("A") + i) if i < 26 else f"X{i+1}"
            lines.append(f'  GRID "G1"  LABEL "{label}"  DIR "X"  COORD {x:g} VISIBLE "Yes"  BUBBLELOC "End"  ')
        for j, y in enumerate(y_coords):
            lines.append(f'  GRID "G1"  LABEL "{j+1}"  DIR "Y"  COORD {y:g} VISIBLE "Yes"  BUBBLELOC "Start"  ')
        lines.append("")

        # ---------------------------------------------------------------------
        # 5. Material Properties
        # ---------------------------------------------------------------------
        from struct_core import parse_units

        parsed_u = parse_units(f"{units_force}, {units_len}")
        lines.append("$ MATERIAL PROPERTIES")
        for mat in model.materials:
            mat_type = _CSI_MATERIAL_TYPES.get(getattr(mat, "type", "elastic"), "Other")
            unit_wt = getattr(mat, "unit_weight", None)
            if unit_wt is None and float(getattr(mat, "rho", 0.0) or 0.0) > 0.0:
                unit_wt = float(mat.rho) * parsed_u.consistent_mass_factor * parsed_u.gravity_acceleration
            weight = f"    WEIGHTPERVOLUME {unit_wt:g}" if unit_wt is not None else ""
            lines.append(f'  MATERIAL  "{mat.id}"    TYPE "{mat_type}"{weight}')
            if mat_type == "Rebar":
                lines.append(f'  MATERIAL  "{mat.id}"    SYMTYPE "Uniaxial"  E {mat.E:g}  A 1.17E-05')
                strengths = f"FY {mat.fy:g}" + (f"  FU {mat.fu:g}" if mat.fu is not None else "")
                lines.append(f'  MATERIAL  "{mat.id}"  {strengths}')
            else:
                lines.append(f'  MATERIAL  "{mat.id}"    SYMTYPE "Isotropic"  E {mat.E:g}  U {mat.nu:g}  A 1.0E-05')
            if mat_type == "Concrete":
                lines.append(f'  MATERIAL  "{mat.id}"    FC {mat.fc:g}')
        lines.append("")

        # ---------------------------------------------------------------------
        # 6. Frame Sections (each with its material) and concrete reinforcement
        # ---------------------------------------------------------------------
        lines.append("$ FRAME SECTIONS")
        for sec in model.sections:
            if isinstance(sec, RectangularSection):
                lines.append(
                    f'  FRAMESECTION  "{sec.id}"  MATERIAL "{sec.material_id}"  '
                    f'SHAPE "Concrete Rectangular"  D {sec.h:g} B {sec.b:g} '
                )
            elif isinstance(sec, CircularSection):
                lines.append(
                    f'  FRAMESECTION  "{sec.id}"  MATERIAL "{sec.material_id}"  '
                    f'SHAPE "Concrete Circle"  D {sec.d:g} '
                )
            else:
                lines.append(f'  FRAMESECTION  "{sec.id}"  MATERIAL "{sec.material_id}"  SHAPE "General" ')
        lines.append("")
        # Bar materials, and for columns the clear cover to the confinement bars (COVER). A beam's COVERTOP /
        # COVERBOTTOM is measured to the bar centroid, which needs bar sizes: not written.
        concrete = _concrete_sections(model)
        if concrete:
            lines.append("$ CONCRETE SECTIONS")
            for sec, role in concrete:
                confine = sec.transverse_rebar_material_id or sec.rebar_material_id
                line = (f'  CONCRETESECTION  "{sec.id}"  LONGBARMATERIAL "{sec.rebar_material_id}"  '
                        f'CONFINEBARMATERIAL "{confine}"  TYPE "{role.title()}"')
                if role == "column":
                    line += '  TRANSREINF "TIES"  DESIGNCHECK "DESIGN"'
                    if sec.cover is not None:
                        line += f"  COVER {sec.cover:g}"
                lines.append(line)
            lines.append("")

        # ---------------------------------------------------------------------
        # 7. Plan Points and Node Coordinate Mapping
        # ---------------------------------------------------------------------
        plan_points: Dict[Tuple[float, float], str] = {}
        for n in sorted(model.nodes, key=lambda node: (node.z, node.y, node.x)):
            key = (round(n.x, 6), round(n.y, 6))
            if key not in plan_points:
                plan_points[key] = str(len(plan_points) + 1)

        node_map: Dict[Union[int, str], Tuple[str, str]] = {}
        node_by_id = {n.id: n for n in model.nodes}
        for n in model.nodes:
            key = (round(n.x, 6), round(n.y, 6))
            pt_id = plan_points[key]
            z_val = round(n.z, 6)
            st_name = "Base" if abs(z_val - z0) < 1e-4 else next(st[0] for st in stories if abs(st[2] - z_val) < 1e-4)
            node_map[n.id] = (pt_id, st_name)

        lines.append("$ POINT COORDINATES")
        for (x, y), pid in sorted(plan_points.items(), key=lambda item: int(item[1]) if item[1].isdigit() else item[1]):
            lines.append(f'  POINT "{pid}"  {x:g} {y:g} ')
        lines.append("")

        # ---------------------------------------------------------------------
        # 8. Element Connectivity Classification (Column, Beam, Brace)
        # ---------------------------------------------------------------------
        col_lines: Dict[str, str] = {}
        beam_spans: Dict[Tuple[str, str], str] = {}
        brace_spans: Dict[Tuple[str, str], str] = {}
        elem_to_line: Dict[Union[int, str], Tuple[str, str, str, str, float]] = {}

        for el in model.elements:
            ni = node_by_id[el.start_node]
            nj = node_by_id[el.end_node]
            pt_i, st_i = node_map[ni.id]
            pt_j, st_j = node_map[nj.id]

            dx = abs(ni.x - nj.x)
            dy = abs(ni.y - nj.y)
            dz = abs(ni.z - nj.z)

            ang = float(getattr(el, "roll_angle", 0.0) or 0.0)

            if dx < 1e-4 and dy < 1e-4 and dz > 1e-4:
                # Vertical Column: CSI convention (v2=+X at Angle=0) vs fem3d (v2=+Y at roll_angle=0)
                etabs_ang = (ang + 90.0 + 180.0) % 360.0 - 180.0
                col_name = f"C{pt_i}"
                col_lines[col_name] = pt_i
                top_story = st_i if ni.z > nj.z else st_j
                elem_to_line[el.id] = (col_name, top_story, "COLUMN", el.section_id, etabs_ang, getattr(el, "release_i", None), getattr(el, "release_j", None))
            elif dz < 1e-4 and (dx > 1e-4 or dy > 1e-4):
                # Horizontal Beam
                span = (pt_i, pt_j) if (int(pt_i) if pt_i.isdigit() else pt_i) < (int(pt_j) if pt_j.isdigit() else pt_j) else (pt_j, pt_i)
                if span not in beam_spans:
                    beam_spans[span] = f"B{len(beam_spans) + 1}"
                bm_name = beam_spans[span]
                elem_to_line[el.id] = (bm_name, st_i, "BEAM", el.section_id, ang, getattr(el, "release_i", None), getattr(el, "release_j", None))
            else:
                # Sloped Brace
                span = (pt_i, pt_j) if (int(pt_i) if pt_i.isdigit() else pt_i) < (int(pt_j) if pt_j.isdigit() else pt_j) else (pt_j, pt_i)
                if span not in brace_spans:
                    brace_spans[span] = f"BR{len(brace_spans) + 1}"
                br_name = brace_spans[span]
                top_story = st_i if ni.z > nj.z else st_j
                elem_to_line[el.id] = (br_name, top_story, "BRACE", el.section_id, ang, getattr(el, "release_i", None), getattr(el, "release_j", None))

        self.elem_to_line = elem_to_line

        lines.append("$ LINE CONNECTIVITIES")
        for col_name, pt_id in sorted(col_lines.items(), key=lambda it: int(it[0][1:]) if it[0][1:].isdigit() else it[0]):
            lines.append(f'  LINE  "{col_name}"  COLUMN  "{pt_id}"  "{pt_id}"  1')
        for (p1, p2), bm_name in sorted(beam_spans.items(), key=lambda it: int(it[1][1:]) if it[1][1:].isdigit() else it[1]):
            lines.append(f'  LINE  "{bm_name}"  BEAM  "{p1}"  "{p2}"  0')
        for (p1, p2), br_name in sorted(brace_spans.items(), key=lambda it: int(it[1][2:]) if it[1][2:].isdigit() else it[1]):
            lines.append(f'  LINE  "{br_name}"  BRACE  "{p1}"  "{p2}"  1')
        lines.append("")

        # ---------------------------------------------------------------------
        # 9. Point Assigns (Boundary Restraints)
        # ---------------------------------------------------------------------
        lines.append("$ POINT ASSIGNS")
        for supp in model.supports:
            ni = node_by_id[supp.node_id]
            pt_id, st_name = node_map[ni.id]
            restrs: List[str] = []
            if supp.ux: restrs.append("UX")
            if supp.uy: restrs.append("UY")
            if supp.uz: restrs.append("UZ")
            if supp.rx: restrs.append("RX")
            if supp.ry: restrs.append("RY")
            if supp.rz: restrs.append("RZ")
            if restrs:
                lines.append(f'  POINTASSIGN  "{pt_id}"  "{st_name}"  RESTRAINT "{" ".join(restrs)}"  ')
        lines.append("")

        # ---------------------------------------------------------------------
        # 10. Line Assigns
        # ---------------------------------------------------------------------
        def _format_rel(ri, rj) -> str:
            tokens = []
            names_i = ["PI", "V2I", "V3I", "TI", "M2I", "M3I"]
            names_j = ["PJ", "V2J", "V3J", "TJ", "M2J", "M3J"]
            if ri:
                for name, active in zip(names_i, ri):
                    if active:
                        tokens.append(name)
            if rj:
                for name, active in zip(names_j, rj):
                    if active:
                        tokens.append(name)
            return f' RELEASE "{" ".join(tokens)}"' if tokens else ""

        lines.append("$ LINE ASSIGNS")
        for el_id, (line_name, story, *_) in elem_to_line.items():
            lines.append(f"; ELEM_MAP {el_id} {line_name} {story}")
        for st in stories:
            st_name = st[0]
            # Columns
            for el_id, (line_name, story, el_type, sec_id, ang, rel_i, rel_j) in elem_to_line.items():
                if el_type == "COLUMN" and story == st_name:
                    ang_str = f" ANG {ang:g}" if abs(ang) > 1e-4 else ""
                    rel_str = _format_rel(rel_i, rel_j)
                    lines.append(f'  LINEASSIGN  "{line_name}"  "{story}"  SECTION "{sec_id}"{ang_str}{rel_str} MINNUMSTA 3 AUTOMESH "YES"  MESHATINTERSECTIONS "YES"  ')
            # Beams
            for el_id, (line_name, story, el_type, sec_id, ang, rel_i, rel_j) in elem_to_line.items():
                if el_type == "BEAM" and story == st_name:
                    ang_str = f" ANG {ang:g}" if abs(ang) > 1e-4 else ""
                    rel_str = _format_rel(rel_i, rel_j)
                    lines.append(f'  LINEASSIGN  "{line_name}"  "{story}"  SECTION "{sec_id}"{ang_str}{rel_str} MINNUMSTA 3 AUTOMESH "YES"  MESHATINTERSECTIONS "YES"  ')
            # Braces
            for el_id, (line_name, story, el_type, sec_id, ang, rel_i, rel_j) in elem_to_line.items():
                if el_type == "BRACE" and story == st_name:
                    ang_str = f" ANG {ang:g}" if abs(ang) > 1e-4 else ""
                    rel_str = _format_rel(rel_i, rel_j)
                    lines.append(f'  LINEASSIGN  "{line_name}"  "{story}"  SECTION "{sec_id}"{ang_str}{rel_str} MINNUMSTA 3 AUTOMESH "YES"  MESHATINTERSECTIONS "YES"  ')
        lines.append("")

        # ---------------------------------------------------------------------
        # 11. Functions (Response Spectrum curves)
        # ---------------------------------------------------------------------
        rsa_cases = [c for c in model.analysis_cases if isinstance(c, ResponseSpectrumAnalysis)]
        if rsa_cases:
            lines.append("$ FUNCTIONS")
            for rsa in rsa_cases:
                func_name = rsa.function_name or "asce7-22"
                sds = rsa.sds if rsa.sds is not None else 1.0
                sd1 = rsa.sd1 if rsa.sd1 is not None else 0.4
                tl = rsa.tl if rsa.tl is not None else 8.0
                damp = rsa.damping_ratio or 0.05
                t0 = 0.2 * sd1 / sds
                ts = sd1 / sds

                t_vals = [0.0, t0, ts]
                for t in [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 7.0, tl, 9.0, 10.0]:
                    if t > ts:
                        t_vals.append(t)
                t_vals = sorted(list(set(round(t, 4) for t in t_vals)))

                pairs: List[str] = []
                for t in t_vals:
                    if t <= t0:
                        sa = sds * (0.4 + 0.6 * t / t0)
                    elif t <= ts:
                        sa = sds
                    elif t <= tl:
                        sa = sd1 / t
                    else:
                        sa = sd1 * tl / (t ** 2)
                    pairs.append(f"{t:g} {sa:.6g}")

                lines.append(f'  FUNCTION "{func_name}"  FUNCTYPE "SPECTRUM"  DAMPRATIO {damp:g}  SPECTYPE "USER"  ')
                lines.append(f'  FUNCTION "{func_name}"  TIMEVAL "{" ".join(pairs)}"  ')
            lines.append("")

        # ---------------------------------------------------------------------
        # 12. Load Patterns
        # ---------------------------------------------------------------------
        lines.append("$ LOAD PATTERNS")
        spec_params = getattr(model, "seismic_parameters", None)
        if spec_params is None and isinstance(model_or_project, Project):
            spec_params = getattr(model_or_project, "seismic_parameters", None)

        auto_seismic_patterns: Dict[str, Dict[str, Any]] = {}
        for lc in model.load_cases:
            pat = str(lc.id)
            upat = pat.upper()
            is_quake = any(q in upat for q in ("QUAKE", "SEISMIC", "EQ"))
            auto_load = getattr(lc, "auto_load", None)
            if not auto_load and is_quake:
                if getattr(lc, "seismic_params", None) or spec_params:
                    auto_load = "ASCE 7-22"

            if auto_load:
                sparams = getattr(lc, "seismic_params", None) or {}
                if not sparams and spec_params:
                    sparams = {
                        "sds": getattr(spec_params, "sds", 1.0),
                        "sd1": getattr(spec_params, "sd1", 0.4),
                        "s1": getattr(spec_params, "s1", 0.35),
                        "tl": getattr(spec_params, "tl", 8.0),
                        "r_factor": getattr(spec_params, "r_factor", 8.0),
                        "omega_0": getattr(spec_params, "omega_0", 3.0),
                        "cd": getattr(spec_params, "cd", 5.5),
                        "importance_factor": getattr(spec_params, "importance_factor", 1.0),
                        "site_class": getattr(spec_params, "site_class", "D"),
                    }
                auto_seismic_patterns[pat] = {
                    "code": str(auto_load),
                    "params": sparams,
                }

            sw = 1 if lc.include_self_weight else 0
            ltype = "Dead" if "dead" in pat.lower() else ("Live" if "live" in pat.lower() else ("Seismic" if is_quake else "Other"))
            lines.append(f'  LOADPATTERN "{pat}"  TYPE  "{ltype}"  SELFWEIGHT  {sw}')

        # Emit SEISMIC definitions for auto lateral load patterns
        top_story = stories[-1][0] if stories else "Story1"
        bottom_story = "Base"
        for pat, info in auto_seismic_patterns.items():
            code = info["code"]
            sp = info["params"]
            dir_val = sp.get("direction", "Y" if "Y" in pat.upper() else "X").upper()
            r_val = float(sp.get("r") or sp.get("r_factor") or 8.0)
            omega_val = float(sp.get("omega") or sp.get("omega_0") or 3.0)
            cd_val = float(sp.get("cd") or 5.5)
            i_val = float(sp.get("i") or sp.get("importance_factor") or 1.0)
            sds_val = float(sp.get("sds") or 1.0)
            sd1_val = float(sp.get("sd1") or 0.4)
            s1_val = float(sp.get("s1") or 0.35)
            tl_val = float(sp.get("tl") or 8.0)
            site_class = str(sp.get("siteclass") or sp.get("site_class") or "D").upper()
            ecc_val = float(sp.get("ecc") or 0.05)

            lines.append(
                f'  SEISMIC "{pat}"  "{code}"  DIR "{dir_val}"  ECC {ecc_val:g}  TOPSTORY "{top_story}"  BOTTOMSTORY "{bottom_story}"  '
                f'PERIODTYPE "APPROX"  CT 0.016  X 0.9  R {r_val:g}  OMEGA {omega_val:g}  CD {cd_val:g}  I {i_val:g}  '
                f'SS {sds_val:g}  S1 {s1_val:g}  SDS {sds_val:g}  SD1 {sd1_val:g}  TL {tl_val:g}  SITECLASS "{site_class}"'
            )
        lines.append("")

        # ---------------------------------------------------------------------
        # 13. Point Object Loads
        # ---------------------------------------------------------------------
        lines.append("$ POINT OBJECT LOADS")
        for lc in model.load_cases:
            if lc.id in auto_seismic_patterns:
                # Do NOT emit manual joint point loads for native auto lateral load pattern!
                continue
            for pl in lc.point_loads:
                pt_id, st_name = node_map[pl.node_id]
                f_parts: List[str] = []
                if abs(pl.fx) > 1e-6: f_parts.append(f"FX {pl.fx:g}")
                if abs(pl.fy) > 1e-6: f_parts.append(f"FY {pl.fy:g}")
                if abs(pl.fz) > 1e-6: f_parts.append(f"FZ {pl.fz:g}")
                if abs(pl.mx) > 1e-6: f_parts.append(f"MX {pl.mx:g}")
                if abs(pl.my) > 1e-6: f_parts.append(f"MY {pl.my:g}")
                if abs(pl.mz) > 1e-6: f_parts.append(f"MZ {pl.mz:g}")
                if f_parts:
                    lines.append(f'  POINTLOAD  "{pt_id}"  "{st_name}"  TYPE "FORCE"  LC "{lc.id}"  {" ".join(f_parts)}')
        lines.append("")

        # ---------------------------------------------------------------------
        # 14. Frame Object Loads
        # ---------------------------------------------------------------------
        lines.append("$ FRAME OBJECT LOADS")
        for lc in model.load_cases:
            for dl in lc.element_loads:
                if dl.element_id not in elem_to_line:
                    continue
                line_name, st_name, el_type, *_ = elem_to_line[dl.element_id]
                rel_a = float(getattr(dl, "start", 0.0) or 0.0)
                rel_b = float(getattr(dl, "end", 1.0) or 1.0)
                is_partial = (rel_a > 1e-6 or rel_b < 0.999999)

                wz = float(getattr(dl, "wz", 0.0) or 0.0)
                wy = float(getattr(dl, "wy", 0.0) or 0.0)
                wx = float(getattr(dl, "wx", 0.0) or 0.0)
                wz_end = float(getattr(dl, "wz_end", None) if getattr(dl, "wz_end", None) is not None else wz)
                wy_end = float(getattr(dl, "wy_end", None) if getattr(dl, "wy_end", None) is not None else wy)
                wx_end = float(getattr(dl, "wx_end", None) if getattr(dl, "wx_end", None) is not None else wx)

                if abs(wz) > 1e-6 or abs(wz_end) > 1e-6:
                    dir_str = "GRAV" if wz < 0 else "Z"
                    va = abs(wz) if wz < 0 else wz
                    vb = abs(wz_end) if wz < 0 else wz_end
                    if is_partial or abs(va - vb) > 1e-6:
                        lines.append(f'  LINELOAD  "{line_name}"  "{st_name}"  TYPE "TRAPF"  DIR "{dir_str}"  LC "{lc.id}"  RELDISTA {rel_a:g}  RELDISTB {rel_b:g}  FVALA {va:g}  FVALB {vb:g}')
                    else:
                        lines.append(f'  LINELOAD  "{line_name}"  "{st_name}"  TYPE "UNIFF"  DIR "{dir_str}"  LC "{lc.id}"  FVAL {va:g}')
                if abs(wx) > 1e-6 or abs(wx_end) > 1e-6:
                    dir_str = "-X" if wx < 0 else "X"
                    va = abs(wx) if wx < 0 else wx
                    vb = abs(wx_end) if wx < 0 else wx_end
                    if is_partial or abs(va - vb) > 1e-6:
                        lines.append(f'  LINELOAD  "{line_name}"  "{st_name}"  TYPE "TRAPF"  DIR "{dir_str}"  LC "{lc.id}"  RELDISTA {rel_a:g}  RELDISTB {rel_b:g}  FVALA {va:g}  FVALB {vb:g}')
                    else:
                        lines.append(f'  LINELOAD  "{line_name}"  "{st_name}"  TYPE "UNIFF"  DIR "{dir_str}"  LC "{lc.id}"  FVAL {va:g}')
                if abs(wy) > 1e-6 or abs(wy_end) > 1e-6:
                    dir_str = "-Y" if wy < 0 else "Y"
                    va = abs(wy) if wy < 0 else wy
                    vb = abs(wy_end) if wy < 0 else wy_end
                    if is_partial or abs(va - vb) > 1e-6:
                        lines.append(f'  LINELOAD  "{line_name}"  "{st_name}"  TYPE "TRAPF"  DIR "{dir_str}"  LC "{lc.id}"  RELDISTA {rel_a:g}  RELDISTB {rel_b:g}  FVALA {va:g}  FVALB {vb:g}')
                    else:
                        lines.append(f'  LINELOAD  "{line_name}"  "{st_name}"  TYPE "UNIFF"  DIR "{dir_str}"  LC "{lc.id}"  FVAL {va:g}')

            for epl in getattr(lc, "element_point_loads", []):
                if epl.element_id not in elem_to_line:
                    continue
                line_name, st_name, el_type, *_ = elem_to_line[epl.element_id]
                pos = float(getattr(epl, "position", 0.0) or 0.0)
                for comp, dir_name in [("fx", "X"), ("fy", "Y"), ("fz", "Z")]:
                    val = float(getattr(epl, comp, 0.0) or 0.0)
                    if abs(val) > 1e-6:
                        actual_dir = dir_name
                        actual_val = val
                        if comp == "fz" and val < 0:
                            actual_dir = "GRAV"
                            actual_val = abs(val)
                        elif val < 0:
                            actual_dir = f"-{dir_name}"
                            actual_val = abs(val)
                        lines.append(f'  POINTLOAD  "{line_name}"  "{st_name}"  TYPE "FORCE"  DIR "{actual_dir}"  LC "{lc.id}"  RELDIST {pos:g}  FVAL {actual_val:g}')
        lines.append("")

        # ---------------------------------------------------------------------
        # 15. Analysis Options
        # ---------------------------------------------------------------------
        lines.append("$ ANALYSIS OPTIONS")
        lines.append('  ACTIVEDOF "UX UY UZ RX RY RZ"  ')
        lines.append("")

        # ---------------------------------------------------------------------
        # 16. Mass Source
        # ---------------------------------------------------------------------
        if model.mass_source:
            ms = model.mass_source
            inc_elem = "Yes" if ms.include_element_mass else "No"
            lines.append("$ MASS SOURCE")
            lines.append(
                f'  MASSSOURCE  "{ms.id}"    INCLUDEELEMENTS "{inc_elem}"    '
                f'INCLUDEADDEDMASS "No"    INCLUDELOADS "Yes"    INCLUDEMOVE "No"    '
                f'INCLUDELATERALMASS "Yes"    INCLUDEVERTICALMASS "No"    LUMPATSTORIES "No"    ISDEFAULT "Yes"  '
            )
            for f in ms.factors:
                lines.append(f'  MASSSOURCELOAD  "{ms.id}"  "{f.load_case_id}"  {f.factor:g} ')
            lines.append("")

        # ---------------------------------------------------------------------
        # 17. Load Cases (Modal, Static, Response Spectrum)
        # ---------------------------------------------------------------------
        lines.append("$ LOAD CASES")
        modal_cases = [c for c in model.analysis_cases if isinstance(c, ModalAnalysis)]
        for mc in modal_cases:
            lines.append(f'  LOADCASE "{mc.id}"  TYPE  "Modal - Eigen"  INITCOND  "PRESET"  ')
            lines.append(f'  LOADCASE "{mc.id}"  ACCEL  "UX"  ')
            lines.append(f'  LOADCASE "{mc.id}"  ACCEL  "UY"  ')
            lines.append(f'  LOADCASE "{mc.id}"  MAXMODES  {mc.num_modes} MINMODES  1 EIGENSHIFTFREQ  0 EIGENCUTOFF  0 EIGENTOL  1E-09 ')

        for lc in model.load_cases:
            lines.append(f'  LOADCASE "{lc.id}"  TYPE  "Linear Static"  INITCOND  "PRESET"  ')
            lines.append(f'  LOADCASE "{lc.id}"  LOADPAT  "{lc.id}"  SF  1 ')

        for rsa in rsa_cases:
            acc_dir = "U1" if rsa.direction == "X" else ("U2" if rsa.direction == "Y" else "U3")
            lines.append(f'  LOADCASE "{rsa.id}"  TYPE  "Response Spectrum"  MODALCASE  "MODAL"  ')
            lines.append(f'  LOADCASE "{rsa.id}"  ACCEL  "{acc_dir}"  FUNC  "{rsa.function_name}"  SF  {rsa.scale_factor:g} ')
            lines.append(f'  LOADCASE "{rsa.id}"  MODALDAMPTYPE  "Constant"  CONSTDAMP  {rsa.damping_ratio:g} ')
        lines.append("")

        # ---------------------------------------------------------------------
        # 18. End of File
        # ---------------------------------------------------------------------
        lines.append("  END")
        lines.append("$ END OF MODEL FILE")
        lines.append("")

        content = "\n".join(lines)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        return path
