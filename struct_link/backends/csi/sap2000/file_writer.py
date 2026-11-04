"""
SAP2000 .s2k file writer.

Exports a struct_core.StructuralModel or Project into a standard SAP2000 text file (.s2k).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Union

from struct_core import Project, StructuralModel, parse_units

from ....base import BaseModelWriter
from ..materials import _CSI_MATERIAL_TYPES, _concrete_sections
from ..s2k_parser import S2kWriter


class Sap2000FileWriter(BaseModelWriter):
    """Exports struct_core.StructuralModel or Project into SAP2000 .s2k text file."""

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

        tables: Dict[str, List[Dict[str, Any]]] = {}

        # Determine units
        u = None
        if isinstance(model_or_project, Project) and model_or_project.metadata and model_or_project.metadata.units:
            u = model_or_project.metadata.units
        elif getattr(model, "units", None):
            try:
                u = parse_units(model.units) if isinstance(model.units, str) else model.units
            except Exception:
                pass
        if u is None:
            raise ValueError("Units unknown: pass units= or use a Project")
        units_str = "KN, m, C"
        if u:
            f = (u.force or "").lower()
            l = (u.length or "").lower()
            if "kip" in f and "in" in l:
                units_str = "Kip, in, F"
            elif "kip" in f and "ft" in l:
                units_str = "Kip, ft, F"
            elif "lb" in f and "in" in l:
                units_str = "Lb, in, F"
            elif "lb" in f and "ft" in l:
                units_str = "Lb, ft, F"
            elif "n" in f and "mm" in l:
                units_str = "N, mm, C"
            elif "kn" in f and "mm" in l:
                units_str = "KN, mm, C"
            elif "kn" in f and "m" in l:
                units_str = "KN, m, C"
            elif "n" in f and "m" in l:
                units_str = "N, m, C"

        # 1. Program Control
        tables["PROGRAM CONTROL"] = [
            {
                "ProgramName": "SAP2000",
                "Version": "24.0.0",
                "ProgLevel": "Ultimate",
                "LicenseOS": True,
                "LicenseSC": True,
                "CurrUnits": units_str,
            }
        ]

        # 2. Active Degrees of Freedom
        tables["ACTIVE DEGREES OF FREEDOM"] = [
            {"UX": True, "UY": True, "UZ": True, "RX": True, "RY": True, "RZ": True}
        ]

        # 3. Coordinate Systems
        tables["COORDINATE SYSTEMS"] = [
            {
                "Name": "GLOBAL",
                "Type": "Cartesian",
                "X": 0.0,
                "Y": 0.0,
                "Z": 0.0,
                "AboutZ": 0.0,
                "AboutY": 0.0,
                "AboutX": 0.0,
            }
        ]

        # 4. Materials: the struct_core type gives the CSI type; only the properties the material has are written
        # (a material without density is written massless). Concrete f'c and rebar Fy / Fu go to tables 03B / 03E.
        parsed_u = parse_units(units_str)
        g_acc = parsed_u.gravity_acceleration
        mat_gen_rows = []
        mat_mech_rows = []
        concrete_rows = []
        rebar_rows = []
        for m in model.materials:
            mid = str(m.id)
            mat_type = _CSI_MATERIAL_TYPES.get(getattr(m, "type", "elastic"), "Other")
            mat_gen_rows.append({
                "Material": mid,
                "Type": mat_type,
                "SymType": "Uniaxial" if mat_type == "Rebar" else "Isotropic",
                "TempDepend": "No",
            })
            E = float(m.E)
            nu = float(m.nu)
            unit_wt = getattr(m, "unit_weight", None)
            if unit_wt is None or float(unit_wt) <= 0.0:
                rho = float(getattr(m, "rho", 0.0) or 0.0)
                unit_wt = rho * parsed_u.consistent_mass_factor * g_acc
            unit_wt = float(unit_wt)
            row = {"Material": mid, "UnitWeight": unit_wt, "UnitMass": unit_wt / g_acc, "E1": E, "A1": 0.0}
            if mat_type != "Rebar":
                row.update({"G12": E / (2.0 * (1.0 + nu)), "U12": nu})
            mat_mech_rows.append(row)
            if mat_type == "Concrete":
                concrete_rows.append({"Material": mid, "Fc": float(m.fc), "eFc": float(m.fc), "LtWtConc": "No"})
            elif mat_type == "Rebar":
                rebar_row = {"Material": mid, "Fy": float(m.fy), "EffFy": float(m.fy)}
                if getattr(m, "fu", None) is not None:
                    rebar_row.update({"Fu": float(m.fu), "EffFu": float(m.fu)})
                rebar_rows.append(rebar_row)

        tables["MATERIAL PROPERTIES 01 - GENERAL"] = mat_gen_rows
        tables["MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES"] = mat_mech_rows
        if concrete_rows:
            tables["MATERIAL PROPERTIES 03B - CONCRETE DATA"] = concrete_rows
        if rebar_rows:
            tables["MATERIAL PROPERTIES 03E - REBAR DATA"] = rebar_rows

        # 5. Frame Section Properties
        sec_rows = []
        for sec in model.sections:
            sec_type = getattr(sec, "type", "general")
            mat_id = sec.material_id
            if sec_type == "rectangular":
                b = float(sec.b)
                h = float(sec.h)
                area = b * h
                i33 = (b * (h ** 3)) / 12.0
                i22 = (h * (b ** 3)) / 12.0
                j = 0.291 * (min(b, h) ** 3) * max(b, h)
                sec_rows.append(
                    {
                        "SectionName": str(sec.id),
                        "Material": mat_id,
                        "Shape": "Rectangular",
                        "t3": h,
                        "t2": b,
                        "Area": area,
                        "TorsConst": j,
                        "I33": i33,
                        "I22": i22,
                        "I23": 0.0,
                        "AS2": (5.0 / 6.0) * area,
                        "AS3": (5.0 / 6.0) * area,
                    }
                )
            elif sec_type == "circular":
                d = float(sec.d)
                r = d / 2.0
                area = math.pi * (r ** 2)
                iz = (math.pi * (d ** 4)) / 64.0
                j = (math.pi * (d ** 4)) / 32.0
                sec_rows.append(
                    {
                        "SectionName": str(sec.id),
                        "Material": mat_id,
                        "Shape": "Circular",
                        "t3": d,
                        "t2": d,
                        "Area": area,
                        "TorsConst": j,
                        "I33": iz,
                        "I22": iz,
                        "I23": 0.0,
                        "AS2": 0.9 * area,
                        "AS3": 0.9 * area,
                    }
                )
            else:
                as2_val = getattr(sec, "As2", None)
                as3_val = getattr(sec, "As3", None)
                gen_dict = {
                    "SectionName": str(sec.id),
                    "Material": mat_id,
                    "Shape": "General",
                    "Area": float(getattr(sec, "A", 1.0)),
                    "I33": float(getattr(sec, "I3", 1.0)),
                    "I22": float(getattr(sec, "I2", 1.0)),
                    "J": float(getattr(sec, "J", 1.0)),
                }
                if as2_val:
                    gen_dict["AS2"] = float(as2_val)
                if as3_val:
                    gen_dict["AS3"] = float(as3_val)
                sec_rows.append(gen_dict)

        tables["FRAME SECTION PROPERTIES 01 - GENERAL"] = sec_rows

        # Reinforcement of concrete sections: bar materials, and for columns the clear cover to the ties ("Cover").
        # A beam's TopCover / BotCover is measured to the bar centroid, which needs bar sizes: not written.
        column_rows, beam_rows = [], []
        for sec, role in _concrete_sections(model):
            row = {"SectionName": str(sec.id), "RebarMatL": str(sec.rebar_material_id),
                   "RebarMatC": str(sec.transverse_rebar_material_id or sec.rebar_material_id)}
            if role == "column":
                row.update({"ReinfConfig": "Rectangular" if sec.type == "rectangular" else "Circular",
                            "LatReinf": "Ties", "ReinfType": "Design"})
                if sec.cover is not None:
                    row["Cover"] = float(sec.cover)
                column_rows.append(row)
            else:
                beam_rows.append(row)
        if column_rows:
            tables["FRAME SECTION PROPERTIES 02 - CONCRETE COLUMN"] = column_rows
        if beam_rows:
            tables["FRAME SECTION PROPERTIES 03 - CONCRETE BEAM"] = beam_rows

        # 6. Load Pattern Definitions, Load Case Definitions & Assignments
        pat_rows = []
        case_rows = []
        static_assign_rows = []

        spec_params = getattr(model, "seismic_parameters", None)
        if spec_params is None and isinstance(model_or_project, Project):
            spec_params = getattr(model_or_project, "seismic_parameters", None)

        all_z = [float(getattr(n, "z", 0.0)) for n in model.nodes]
        max_z = max(all_z) if all_z else 7.0
        sup_node_ids = {str(s.node_id) for s in model.supports}
        sup_z = [float(getattr(n, "z", 0.0)) for n in model.nodes if str(n.id) in sup_node_ids]
        min_z = min(sup_z) if sup_z else (min(all_z) if all_z else 0.0)

        auto_seismic_rows = []
        auto_seismic_patterns = set()

        load_cases = getattr(model, "load_cases", [])
        if load_cases:
            for lc in load_cases:
                pat = str(lc.id)
                upat = pat.upper()
                is_dead = "DEAD" in upat
                is_live = "LIVE" in upat
                is_wind = "WIND" in upat
                is_quake = any(q in upat for q in ("QUAKE", "SEISMIC", "EQ"))
                is_snow = "SNOW" in upat

                if is_dead:
                    des_type = "Dead"
                elif is_live:
                    des_type = "Live"
                elif is_wind:
                    des_type = "Wind"
                elif is_quake:
                    des_type = "Quake"
                elif is_snow:
                    des_type = "Snow"
                else:
                    des_type = "Other"

                self_wt_mult = 1.0 if getattr(lc, "include_self_weight", False) else 0.0

                pat_row = {
                    "LoadPat": pat,
                    "DesignType": des_type,
                    "SelfWtMult": self_wt_mult,
                }
                auto_load = getattr(lc, "auto_load", None)
                if not auto_load and is_quake:
                    if getattr(lc, "seismic_params", None) or spec_params:
                        auto_load = "ASCE 7-22"

                if auto_load:
                    pat_row["AutoLoad"] = str(auto_load)
                    if str(auto_load).upper() == "ASCE 7-22":
                        auto_seismic_patterns.add(pat)
                        sparams = getattr(lc, "seismic_params", None) or {}
                        if not sparams and spec_params:
                            sparams = {
                                "sds": getattr(spec_params, "sds", 1.0),
                                "sd1": getattr(spec_params, "sd1", 0.4),
                                "s1": getattr(spec_params, "s1", 0.35),
                                "tl": getattr(spec_params, "tl", 8.0),
                                "r_factor": getattr(spec_params, "r_factor", 8.0),
                                "importance_factor": getattr(spec_params, "importance_factor", 1.0),
                                "site_class": getattr(spec_params, "site_class", "D"),
                                "system_type": getattr(spec_params, "system_type", "rc_smf"),
                                "method": getattr(spec_params, "method", 2),
                            }

                        dir_val = sparams.get("direction", "Y" if "Y" in upat else "X")
                        stype = str(sparams.get("system_type", "rc_smf")).lower()
                        if "steel" in stype:
                            ct_and_x = "0.028ft, 0.8"
                        elif "brace" in stype:
                            ct_and_x = "0.030ft, 0.75"
                        else:
                            ct_and_x = "0.016ft, 0.9"

                        r_val = float(sparams.get("r_factor") or 8.0)
                        omega_val = float(sparams.get("omega") or 3.0)
                        cd_val = float(sparams.get("cd") or 5.5)
                        i_val = float(sparams.get("importance_factor") or 1.0)
                        sds_val = float(sparams.get("sds") or 1.0)
                        sd1_val = float(sparams.get("sd1") or 0.4)
                        s1_val = float(sparams.get("s1") if sparams.get("s1") is not None else round(sd1_val * 0.875, 4))
                        tl_val = float(sparams.get("tl") or 8.0)
                        site_class = str(sparams.get("site_class") or "D")
                        method_type = f"Method {sparams.get('method') or 2}"

                        hn = max(0.1, max_z - min_z)
                        t_used = float(sparams.get("period_used") or (0.0466 * (hn ** 0.9)))
                        cs_used = float(sparams.get("coeff_used") or (sds_val / (r_val / i_val)))
                        w_used = float(sparams.get("weight_used") or 1028.72)
                        vb_val = float(sparams.get("base_shear") or (cs_used * w_used))

                        auto_seismic_rows.append({
                            "LoadPat": pat,
                            "Dir": dir_val,
                            "PercentEcc": float(sparams.get("percent_ecc") or 0.0),
                            "EccOverride": False,
                            "PeriodCalc": "Approximate",
                            "CtAndX": ct_and_x,
                            "UserZ": True,
                            "MaxZ": max_z,
                            "MinZ": min_z,
                            "MethodType": method_type,
                            "R": int(r_val) if r_val.is_integer() else r_val,
                            "Omega": int(omega_val) if omega_val.is_integer() else omega_val,
                            "Cd": cd_val,
                            "I": int(i_val) if i_val.is_integer() else i_val,
                            "Sds": sds_val,
                            "Sd1": sd1_val,
                            "S1": s1_val,
                            "TL": int(tl_val) if tl_val.is_integer() else tl_val,
                            "SiteClass": site_class,
                            "TUsed": t_used,
                            "CoeffUsed": cs_used,
                            "WeightUsed": w_used,
                            "BaseShear": vb_val,
                        })
                elif des_type in ("Quake", "Wind", "Wave", "Snow"):
                    pat_row["AutoLoad"] = "None"

                pat_rows.append(pat_row)

                case_rows.append(
                    {
                        "Case": pat,
                        "Type": "LinStatic",
                        "InitialCond": "Zero",
                        "DesignType": des_type,
                    }
                )

                static_assign_rows.append(
                    {
                        "Case": pat,
                        "LoadType": "Load pattern",
                        "LoadName": pat,
                        "LoadSF": 1.0,
                    }
                )
        else:
            pat_rows.append({"LoadPat": "DEAD", "DesignType": "Dead", "SelfWtMult": 1.0})
            case_rows.append({"Case": "DEAD", "Type": "LinStatic", "InitialCond": "Zero", "DesignType": "Dead"})
            static_assign_rows.append({"Case": "DEAD", "LoadType": "Load pattern", "LoadName": "DEAD", "LoadSF": 1.0})

        # Add MODAL dynamic load case
        modal_cases = [ac for ac in getattr(model, "analysis_cases", []) if getattr(ac, "type", "") == "modal"]
        n_modes = modal_cases[0].num_modes if modal_cases else 12
        modal_case_name = str(modal_cases[0].id) if modal_cases else "MODAL"
        case_rows.append(
            {
                "Case": modal_case_name,
                "Type": "LinModal",
                "InitialCond": "Zero",
                "DesignType": "Other",
            }
        )
        modal_rows = [
            {
                "Case": modal_case_name,
                "ModeType": "Eigen",
                "MaxNumModes": n_modes,
                "MinNumModes": 1,
            }
        ]

        # Add RESPONSE SPECTRUM dynamic load cases if defined
        rsa_cases = [ac for ac in getattr(model, "analysis_cases", []) if getattr(ac, "type", "") == "response_spectrum"]
        rsa_gen_rows = []
        rsa_assign_rows = []
        for rsa in rsa_cases:
            rcid = str(rsa.id)
            case_rows.append({
                "Case": rcid,
                "Type": "LinRespSpec",
                "InitialCond": "Zero",
                "DesignType": "Quake",
            })
            rsa_gen_rows.append({
                "Case": rcid,
                "ModalCombo": getattr(rsa, "combination", "CQC"),
                "GMCf1": 1.0,
                "GMCf2": 0.0,
                "PerRigid": "SRSS",
                "DirCombo": "SRSS",
                "MotionType": "Acceleration",
                "DampingType": "Constant",
                "ConstDamp": float(getattr(rsa, "damping_ratio", 0.05)),
                "EccenRatio": 0.0,
                "NumOverride": 0,
            })
            dir_str = str(getattr(rsa, "direction", "X")).upper()
            u_dir = "U1" if dir_str == "X" else ("U2" if dir_str == "Y" else "U3")
            func_name = str(getattr(rsa, "function_name", "asce7-22"))
            rsa_assign_rows.append({
                "Case": rcid,
                "LoadType": "Acceleration",
                "LoadName": u_dir,
                "CoordSys": "GLOBAL",
                "Function": func_name,
                "Angle": 0.0,
                "TransAccSF": float(getattr(rsa, "scale_factor", 9.80665)),
            })

        tables["LOAD PATTERN DEFINITIONS"] = pat_rows
        if auto_seismic_rows:
            tables["AUTO SEISMIC - ASCE 7-22"] = auto_seismic_rows
        tables["LOAD CASE DEFINITIONS"] = case_rows
        tables["CASE - STATIC 1 - LOAD ASSIGNMENTS"] = static_assign_rows
        tables["CASE - MODAL 1 - GENERAL"] = modal_rows
        if rsa_gen_rows:
            tables["CASE - RESPONSE SPECTRUM 1 - GENERAL"] = rsa_gen_rows
            tables["CASE - RESPONSE SPECTRUM 2 - LOAD ASSIGNMENTS"] = rsa_assign_rows

        # Response spectrum function table (ASCE 7-22)
        spec_params = getattr(model, "seismic_parameters", None)
        if spec_params is None and isinstance(model_or_project, Project):
            spec_params = getattr(model_or_project, "seismic_parameters", None)
        if spec_params is None and rsa_cases:
            for rsa in rsa_cases:
                if getattr(rsa, "sds", None) is not None and getattr(rsa, "sd1", None) is not None:
                    from struct_load import SeismicParameters
                    spec_params = SeismicParameters(
                        sds=float(rsa.sds),
                        sd1=float(rsa.sd1),
                        tl=float(getattr(rsa, "tl", 8.0) or 8.0),
                        site_class=str(getattr(rsa, "site_class", "D") or "D"),
                        risk_category=str(getattr(rsa, "risk_category", "II") or "II"),
                        system_type=str(getattr(rsa, "system_type", "rc_smf") or "rc_smf"),
                        damping_ratio=float(getattr(rsa, "damping_ratio", 0.05)),
                    )
                    break
        if spec_params is not None:
            try:
                from struct_load import calculate_design_response_spectrum
                spec_data = calculate_design_response_spectrum(spec_params)
                fn_name = "asce7-22"
                func_rows = []
                for idx, (t_val, sa_val) in enumerate(zip(spec_data["periods"], spec_data["sa"])):
                    row = {
                        "Name": fn_name,
                        "Period": float(t_val),
                        "Accel": float(sa_val),
                    }
                    if idx == 0:
                        row["FuncDamp"] = float(getattr(spec_params, "damping_ratio", 0.05))
                        row["Direction"] = "Horizontal"
                        row["SpecType"] = "Design"
                        row["SiteClass"] = str(getattr(spec_params, "site_class", "D"))
                        row["Ssd"] = float(getattr(spec_params, "sds", 1.0))
                        row["Sd1"] = float(getattr(spec_params, "sd1", 0.4))
                        row["TL"] = float(getattr(spec_params, "tl", 8.0))
                    func_rows.append(row)
                tables["FUNCTION - RESPONSE SPECTRUM - ASCE 7-22"] = func_rows
            except Exception:
                pass

        # 7. Joint Coordinates
        coord_rows = []
        jt_mass_rows = []
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
            m = getattr(n, "mass", 0.0)
            if m and float(m) > 0.0:
                jt_mass_rows.append({
                    "Joint": str(n.id),
                    "U1": float(m),
                    "U2": float(m),
                    "U3": float(m),
                    "R1": 0.0,
                    "R2": 0.0,
                    "R3": 0.0,
                })
        tables["JOINT COORDINATES"] = coord_rows
        if jt_mass_rows:
            tables["JOINT ADDED MASS ASSIGNMENTS"] = jt_mass_rows

        # 8. Connectivity - Frame & Section Assignments
        conn_rows = []
        sec_assign_rows = []

        node_coords = {str(n.id): n for n in model.nodes}
        local_axes_rows = []

        release_rows = []
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
            sec_name = str(el.section_id) if getattr(el, "section_id", None) is not None else None
            sec_assign_rows.append(
                {
                    "Frame": fid,
                    "SectionName": sec_name,
                    "AutoSelect": "N.A.",
                    "AnalSect": sec_name,
                }
            )

            # Local axes assignment
            # In fem3d/struct_core:
            # - For horizontal/inclined members: default matches SAP2000 Angle = 0.
            # - For vertical members pointing +Z: fem3d default has v2=+Y, v3=-X,
            #   which corresponds to SAP2000 Angle = 90.
            roll_angle = float(getattr(el, "roll_angle", 0.0) or 0.0)
            ni = node_coords.get(str(el.start_node))
            nj = node_coords.get(str(el.end_node))
            if ni and nj:
                dx = float(nj.x - ni.x)
                dy = float(nj.y - ni.y)
                dz = float(getattr(nj, "z", 0.0) - getattr(ni, "z", 0.0))
                is_vertical = (dx**2 + dy**2) < 1e-12 and abs(dz) > 1e-12
                if is_vertical:
                    sap_angle = roll_angle + 90.0
                else:
                    sap_angle = roll_angle

                # Normalize angle to (-180, 180]
                sap_angle = (sap_angle + 180.0) % 360.0 - 180.0
                if abs(sap_angle) > 1e-6:
                    local_axes_rows.append(
                        {
                            "Frame": fid,
                            "Angle": sap_angle,
                            "AdvanceAxes": "No",
                        }
                    )

            # Frame end releases in member local axes [P, V2, V3, T, M2, M3]
            rel_i = getattr(el, "release_i", None)
            rel_j = getattr(el, "release_j", None)
            if (rel_i and any(rel_i)) or (rel_j and any(rel_j)):
                ri = rel_i or [False] * 6
                rj = rel_j or [False] * 6
                release_rows.append(
                    {
                        "Frame": fid,
                        "PI": bool(ri[0]),
                        "V2I": bool(ri[1]),
                        "V3I": bool(ri[2]),
                        "TI": bool(ri[3]),
                        "M2I": bool(ri[4]),
                        "M3I": bool(ri[5]),
                        "PJ": bool(rj[0]),
                        "V2J": bool(rj[1]),
                        "V3J": bool(rj[2]),
                        "TJ": bool(rj[3]),
                        "M2J": bool(rj[4]),
                        "M3J": bool(rj[5]),
                    }
                )

        tables["CONNECTIVITY - FRAME"] = conn_rows
        tables["FRAME SECTION ASSIGNMENTS"] = sec_assign_rows
        if local_axes_rows:
            tables["FRAME LOCAL AXES ASSIGNMENTS 1 - TYPICAL"] = local_axes_rows
        if release_rows:
            tables["FRAME RELEASE ASSIGNMENTS 1 - GENERAL"] = release_rows

        prop_mod_rows = []
        for el in model.elements:
            mods = getattr(el, "stiffness_modifiers", None)
            if mods:
                prop_mod_rows.append({
                    "Frame": str(el.id),
                    "A": float(mods.get("A") or 1.0),
                    "A2": float(mods.get("As2") or mods.get("A2") or 1.0),
                    "A3": float(mods.get("As3") or mods.get("A3") or 1.0),
                    "J": float(mods.get("J") or 1.0),
                    "I2": float(mods.get("I2") or 1.0),
                    "I3": float(mods.get("I3") or 1.0),
                    "Mass": float(mods.get("mass") or 1.0),
                    "Weight": float(mods.get("weight") or 1.0),
                })
        if prop_mod_rows:
            tables["FRAME PROPERTY MODIFIERS"] = prop_mod_rows

        # 9. Joint Restraint Assignments
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
            tables["JOINT RESTRAINT ASSIGNMENTS"] = restr_rows

        # 10. Loads
        jt_load_rows = []
        auto_seismic_joint_rows = []
        frm_load_rows = []
        node_coords = {str(n.id): n for n in model.nodes}

        for lc in load_cases:
            pat = str(lc.id)
            is_auto_seismic = pat in auto_seismic_patterns

            for pl in getattr(lc, "point_loads", []):
                nid = str(pl.node_id)
                n = node_coords.get(nid)
                if is_auto_seismic:
                    auto_seismic_joint_rows.append(
                        {
                            "LoadPat": pat,
                            "AutoLdType": "ASCE 7-22",
                            "JointElem": nid,
                            "Joint": nid,
                            "FX": float(getattr(pl, "fx", 0.0)),
                            "FY": float(getattr(pl, "fy", 0.0)),
                            "FZ": float(getattr(pl, "fz", 0.0)),
                            "MX": float(getattr(pl, "mx", 0.0)),
                            "MY": float(getattr(pl, "my", 0.0)),
                            "MZ": float(getattr(pl, "mz", 0.0)),
                            "X": float(n.x) if n else 0.0,
                            "Y": float(n.y) if n else 0.0,
                            "Z": float(getattr(n, "z", 0.0)) if n else 0.0,
                        }
                    )
                else:
                    jt_load_rows.append(
                        {
                            "Joint": nid,
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
                rel_a = float(getattr(dl, "start", 0.0) or 0.0)
                rel_b = float(getattr(dl, "end", 1.0) or 1.0)
                is_loc = getattr(dl, "is_local", False)

                if is_loc:
                    w1 = float(getattr(dl, "w1", 0.0) or 0.0)
                    w2 = float(getattr(dl, "w2", 0.0) or 0.0)
                    w3 = float(getattr(dl, "w3", 0.0) or 0.0)
                    w1_end = float(getattr(dl, "w1_end", None) if getattr(dl, "w1_end", None) is not None else w1)
                    w2_end = float(getattr(dl, "w2_end", None) if getattr(dl, "w2_end", None) is not None else w2)
                    w3_end = float(getattr(dl, "w3_end", None) if getattr(dl, "w3_end", None) is not None else w3)
                    for dir_str, va, vb in [("1", w1, w1_end), ("2", w2, w2_end), ("3", w3, w3_end)]:
                        if abs(va) > 1e-12 or abs(vb) > 1e-12:
                            frm_load_rows.append({
                                "Frame": str(dl.element_id),
                                "LoadPat": pat,
                                "CoordSys": "LOCAL",
                                "Type": "Force",
                                "Dir": dir_str,
                                "DistType": "RelDist",
                                "RelDistA": rel_a,
                                "RelDistB": rel_b,
                                "FOverLA": float(va),
                                "FOverLB": float(vb),
                                "FValA": float(va),
                                "FValB": float(vb),
                            })
                else:
                    wz = float(getattr(dl, "wz", 0.0) or 0.0)
                    wy = float(getattr(dl, "wy", 0.0) or 0.0)
                    wx = float(getattr(dl, "wx", 0.0) or 0.0)
                    wz_end = float(getattr(dl, "wz_end", None) if getattr(dl, "wz_end", None) is not None else wz)
                    wy_end = float(getattr(dl, "wy_end", None) if getattr(dl, "wy_end", None) is not None else wy)
                    wx_end = float(getattr(dl, "wx_end", None) if getattr(dl, "wx_end", None) is not None else wx)

                    if abs(wz) > 1e-12 or abs(wz_end) > 1e-12:
                        dir_str = "Gravity" if wz < 0 else "Z"
                        val_a = abs(wz) if wz < 0 else wz
                        val_b = abs(wz_end) if wz < 0 else wz_end
                        frm_load_rows.append({
                            "Frame": str(dl.element_id),
                            "LoadPat": pat,
                            "CoordSys": "GLOBAL",
                            "Type": "Force",
                            "Dir": dir_str,
                            "DistType": "RelDist",
                            "RelDistA": rel_a,
                            "RelDistB": rel_b,
                            "FOverLA": float(val_a),
                            "FOverLB": float(val_b),
                            "FValA": float(val_a),
                            "FValB": float(val_b),
                        })
                    if abs(wy) > 1e-12 or abs(wy_end) > 1e-12:
                        dir_str = "-Y" if wy < 0 else "Y"
                        val_a = abs(wy) if wy < 0 else wy
                        val_b = abs(wy_end) if wy < 0 else wy_end
                        frm_load_rows.append({
                            "Frame": str(dl.element_id),
                            "LoadPat": pat,
                            "CoordSys": "GLOBAL",
                            "Type": "Force",
                            "Dir": dir_str,
                            "DistType": "RelDist",
                            "RelDistA": rel_a,
                            "RelDistB": rel_b,
                            "FOverLA": float(val_a),
                            "FOverLB": float(val_b),
                            "FValA": float(val_a),
                            "FValB": float(val_b),
                        })
                    if abs(wx) > 1e-12 or abs(wx_end) > 1e-12:
                        dir_str = "-X" if wx < 0 else "X"
                        val_a = abs(wx) if wx < 0 else wx
                        val_b = abs(wx_end) if wx < 0 else wx_end
                        frm_load_rows.append({
                            "Frame": str(dl.element_id),
                            "LoadPat": pat,
                            "CoordSys": "GLOBAL",
                            "Type": "Force",
                            "Dir": dir_str,
                            "DistType": "RelDist",
                            "RelDistA": rel_a,
                            "RelDistB": rel_b,
                            "FOverLA": float(val_a),
                            "FOverLB": float(val_b),
                            "FValA": float(val_a),
                            "FValB": float(val_b),
                        })

        if jt_load_rows:
            tables["JOINT LOADS - FORCE"] = jt_load_rows
        if auto_seismic_joint_rows:
            tables["AUTO SEISMIC LOADS TO JOINTS"] = auto_seismic_joint_rows
        if frm_load_rows:
            tables["FRAME LOADS - DISTRIBUTED"] = frm_load_rows

        frm_pt_rows = []
        for lc in getattr(model, "load_cases", []):
            pat = str(lc.id)
            for epl in getattr(lc, "element_point_loads", []):
                is_loc = getattr(epl, "is_local", False)
                csys = "LOCAL" if is_loc else "GLOBAL"
                pos = float(getattr(epl, "position", 0.0) or 0.0)
                for comp, dir_name, ltype in [
                    ("fx", "X" if not is_loc else "1", "Force"),
                    ("fy", "Y" if not is_loc else "2", "Force"),
                    ("fz", "Z" if not is_loc else "3", "Force"),
                    ("mx", "X" if not is_loc else "1", "Moment"),
                    ("my", "Y" if not is_loc else "2", "Moment"),
                    ("mz", "Z" if not is_loc else "3", "Moment"),
                ]:
                    val = float(getattr(epl, comp, 0.0) or 0.0)
                    if abs(val) > 1e-12:
                        actual_dir = dir_name
                        actual_val = val
                        if not is_loc and ltype == "Force" and comp == "fz" and val < 0:
                            actual_dir = "Gravity"
                            actual_val = abs(val)
                        elif not is_loc and val < 0:
                            actual_dir = f"-{dir_name}"
                            actual_val = abs(val)
                        frm_pt_rows.append({
                            "Frame": str(epl.element_id),
                            "LoadPat": pat,
                            "CoordSys": csys,
                            "Type": ltype,
                            "Dir": actual_dir,
                            "DistType": "RelDist",
                            "RelDist": pos,
                            "PointLoad": actual_val,
                        })
        if frm_pt_rows:
            tables["FRAME LOADS - POINT"] = frm_pt_rows

        comb_rows = []
        for comb in getattr(model, "load_combinations", []):
            cname = str(comb.id)
            for f in getattr(comb, "factors", []):
                comb_rows.append({
                    "ComboName": cname,
                    "CaseName": str(f.load_case_id),
                    "ScaleFactor": float(f.factor),
                    "ComboType": "Linear Add",
                })
        if comb_rows:
            tables["COMBINATION DEFINITIONS"] = comb_rows

        # 11. Mass Source
        if getattr(model, "mass_source", None):
            ms = model.mass_source
            ms_id = str(getattr(ms, "id", None) or "MSSSRC1")
            ms_rows = []
            factors = getattr(ms, "factors", [])
            inc_elem = bool(getattr(ms, "include_element_mass", False))

            has_dead_factor = any(
                str(f.load_case_id).upper() == "DEAD" and float(f.factor) > 0.0
                for f in factors
            )
            elements_str = "Yes" if (inc_elem and not has_dead_factor) else "No"
            loads_str = "Yes" if factors else "No"

            if factors:
                for idx, f in enumerate(factors):
                    row = {
                        "MassSource": ms_id,
                    }
                    if idx == 0:
                        row["Elements"] = elements_str
                        row["Masses"] = "No"
                        row["Loads"] = loads_str
                        row["IsDefault"] = "Yes"
                    row["LoadPat"] = str(f.load_case_id)
                    row["Multiplier"] = float(f.factor)
                    ms_rows.append(row)
            else:
                ms_rows.append({
                    "MassSource": ms_id,
                    "Elements": elements_str,
                    "Masses": "No",
                    "Loads": "No",
                    "IsDefault": "Yes",
                })
            tables["MASS SOURCE"] = ms_rows

        return S2kWriter.write_tables(tables, target_path)
