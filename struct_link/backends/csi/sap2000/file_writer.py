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

        # Determine units
        units_str = "KN, m, C"
        if isinstance(model_or_project, Project) and model_or_project.metadata and model_or_project.metadata.units:
            u = model_or_project.metadata.units
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

        # 4. Materials
        default_mat_id = model.materials[0].id if model.materials else "CONC"
        materials = model.materials if model.materials else []

        mat_gen_rows = []
        mat_mech_rows = []
        for m in materials:
            mid = str(m.id)
            m_name = getattr(m, "name", None) or ""
            if m_name in ("Concrete", "Steel", "Rebar", "Tendon", "Aluminum", "ColdFormed"):
                mat_type = m_name
            elif any(s in mid.upper() for s in ("STEEL", "A992", "A36", "A500", "A53")):
                mat_type = "Steel"
            elif any(s in mid.upper() for s in ("REBAR", "A615", "A706")):
                mat_type = "Rebar"
            elif any(s in mid.upper() for s in ("TENDON", "A416")):
                mat_type = "Tendon"
            elif any(s in mid.upper() for s in ("CONC", "4000PSI", "3000PSI", "5000PSI")):
                mat_type = "Concrete"
            else:
                mat_type = "Steel" if getattr(m, "E", 0) > 1e8 else "Concrete"

            sym_type = "Isotropic" if mat_type in ("Concrete", "Steel", "Aluminum") else "Uniaxial"

            mat_gen_rows.append(
                {
                    "Material": mid,
                    "Type": mat_type,
                    "SymType": sym_type,
                    "TempDepend": "No",
                }
            )

            parsed_u = parse_units(units_str)
            g_acc = parsed_u.gravity_acceleration

            E = float(getattr(m, "E", 2.5e7 if mat_type == "Concrete" else 2.0e8))
            nu = float(getattr(m, "nu", 0.2 if mat_type == "Concrete" else 0.3))
            G = float(getattr(m, "G", E / (2.0 * (1.0 + nu))))
            unit_wt = getattr(m, "unit_weight", None)
            if unit_wt is not None and float(unit_wt) > 0.0:
                unit_wt = float(unit_wt)
            else:
                rho_val = getattr(m, "rho", None)
                if rho_val is not None and float(rho_val) > 0.0:
                    rho_f = float(rho_val)
                    if "kn" in units_str.lower():
                        unit_wt = (rho_f * g_acc) / 1000.0
                    else:
                        unit_wt = rho_f * g_acc
                else:
                    if mat_type == "Concrete":
                        unit_wt = 24.0 if "kn" in units_str.lower() else (2400.0 * g_acc)
                    else:
                        unit_wt = 76.97 if "kn" in units_str.lower() else (7850.0 * g_acc)

            # In SAP2000, UnitMass is the consistent mass density (Force * s^2 / L^4 = UnitWeight / g)
            unit_mass = unit_wt / g_acc

            mat_mech_rows.append(
                {
                    "Material": mid,
                    "UnitWeight": unit_wt,
                    "UnitMass": unit_mass,
                    "E1": E,
                    "G12": G,
                    "U12": nu,
                    "A1": 0.0,
                }
            )

        if not mat_mech_rows:
            parsed_u = parse_units(units_str)
            g_acc = parsed_u.gravity_acceleration
            conc_wt = 24.0 if "kn" in units_str.lower() else (2400.0 * g_acc)
            conc_mass = conc_wt / g_acc
            mat_gen_rows.append({"Material": "CONC", "Type": "Concrete", "SymType": "Isotropic", "TempDepend": "No"})
            mat_mech_rows.append(
                {
                    "Material": "CONC",
                    "UnitWeight": conc_wt,
                    "UnitMass": conc_mass,
                    "E1": 2.5e7,
                    "G12": 1.0416666e7,
                    "U12": 0.2,
                    "A1": 0.0,
                }
            )

        tables["MATERIAL PROPERTIES 01 - GENERAL"] = mat_gen_rows
        tables["MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES"] = mat_mech_rows

        # 5. Frame Section Properties
        sec_rows = []
        for sec in model.sections:
            sec_type = getattr(sec, "type", "general")
            mat_id = getattr(sec, "material_id", default_mat_id)
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
                as2_val = getattr(sec, "As2", getattr(sec, "as2", getattr(sec, "Ay", getattr(sec, "AS2", 0.0))))
                as3_val = getattr(sec, "As3", getattr(sec, "as3", getattr(sec, "Az", getattr(sec, "AS3", 0.0))))
                gen_dict = {
                    "SectionName": str(sec.id),
                    "Material": mat_id,
                    "Shape": "General",
                    "Area": float(getattr(sec, "A", 1.0)),
                    "I33": float(getattr(sec, "Iz", 1.0)),
                    "I22": float(getattr(sec, "Iy", 1.0)),
                    "J": float(getattr(sec, "J", 1.0)),
                }
                if as2_val:
                    gen_dict["AS2"] = float(as2_val)
                if as3_val:
                    gen_dict["AS3"] = float(as3_val)
                sec_rows.append(gen_dict)

        if not sec_rows:
            sec_rows.append(
                {
                    "SectionName": "DEFAULT_SEC",
                    "Material": default_mat_id,
                    "Shape": "Rectangular",
                    "t3": 0.4,
                    "t2": 0.4,
                    "Area": 0.16,
                    "I33": 0.002133,
                    "I22": 0.002133,
                }
            )
        tables["FRAME SECTION PROPERTIES 01 - GENERAL"] = sec_rows

        # 6. Load Pattern Definitions, Load Case Definitions & Assignments
        pat_rows = []
        case_rows = []
        static_assign_rows = []

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
                if auto_load:
                    pat_row["AutoLoad"] = str(auto_load)
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

        # 8. Connectivity - Frame & Section Assignments
        conn_rows = []
        sec_assign_rows = []
        default_sec = model.sections[0].id if model.sections else "DEFAULT_SEC"

        node_coords = {str(n.id): n for n in model.nodes}
        local_axes_rows = []

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

        tables["CONNECTIVITY - FRAME"] = conn_rows
        tables["FRAME SECTION ASSIGNMENTS"] = sec_assign_rows
        if local_axes_rows:
            tables["FRAME LOCAL AXES ASSIGNMENTS 1 - TYPICAL"] = local_axes_rows

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
        frm_load_rows = []

        for lc in load_cases:
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
                wz = float(getattr(dl, "wz", 0.0) or 0.0)
                wy = float(getattr(dl, "wy", 0.0) or 0.0)
                wx = float(getattr(dl, "wx", 0.0) or 0.0)

                if wz < 0:
                    dir_str = "Gravity"
                    val = abs(wz)
                elif wz > 0:
                    dir_str = "Z"
                    val = wz
                elif wy < 0:
                    dir_str = "-Y"
                    val = abs(wy)
                elif wy > 0:
                    dir_str = "Y"
                    val = wy
                elif wx < 0:
                    dir_str = "-X"
                    val = abs(wx)
                else:
                    dir_str = "X"
                    val = wx

                val_a = float(getattr(dl, "w_start", None) or val)
                val_b = float(getattr(dl, "w_end", None) or val)

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
                        "FOverLA": float(abs(val_a)),
                        "FOverLB": float(abs(val_b)),
                        "FValA": float(abs(val_a)),
                        "FValB": float(abs(val_b)),
                    }
                )

        if jt_load_rows:
            tables["JOINT LOADS - FORCE"] = jt_load_rows
        if frm_load_rows:
            tables["FRAME LOADS - DISTRIBUTED"] = frm_load_rows

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
