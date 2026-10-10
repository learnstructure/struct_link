"""
SAP2000 .s2k file writer.

Exports a struct_core.StructuralModel or Project into a standard SAP2000 text file (.s2k).
"""

from __future__ import annotations

import math
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

            rho = float(getattr(m, "rho", 2400.0 if mat_type == "Concrete" else 7850.0))
            E = float(getattr(m, "E", 2.5e7 if mat_type == "Concrete" else 2.0e8))
            nu = float(getattr(m, "nu", 0.2 if mat_type == "Concrete" else 0.3))
            G = float(getattr(m, "G", E / (2.0 * (1.0 + nu))))
            unit_wt = getattr(m, "unit_weight", None)
            if unit_wt is None:
                unit_wt = rho * 9.81 if rho > 10 else rho * 386.4
            else:
                unit_wt = float(unit_wt)

            mat_mech_rows.append(
                {
                    "Material": mid,
                    "UnitMass": rho,
                    "UnitWeight": unit_wt,
                    "E1": E,
                    "G12": G,
                    "U12": nu,
                    "A1": 0.0,
                }
            )

        if not mat_mech_rows:
            mat_gen_rows.append({"Material": "CONC", "Type": "Concrete", "SymType": "Isotropic", "TempDepend": "No"})
            mat_mech_rows.append(
                {
                    "Material": "CONC",
                    "UnitMass": 2400.0,
                    "UnitWeight": 23544.0,
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

                pat_rows.append(
                    {
                        "LoadPat": pat,
                        "DesignType": des_type,
                        "SelfWtMult": self_wt_mult,
                    }
                )

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

        # Add default MODAL dynamic load case
        case_rows.append(
            {
                "Case": "MODAL",
                "Type": "LinModal",
                "InitialCond": "Zero",
                "DesignType": "Other",
            }
        )
        modal_rows = [
            {
                "Case": "MODAL",
                "ModeType": "Eigen",
                "MaxNumModes": 12,
                "MinNumModes": 1,
            }
        ]

        tables["LOAD PATTERN DEFINITIONS"] = pat_rows
        tables["LOAD CASE DEFINITIONS"] = case_rows
        tables["CASE - STATIC 1 - LOAD ASSIGNMENTS"] = static_assign_rows
        tables["CASE - MODAL 1 - GENERAL"] = modal_rows

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

        tables["CONNECTIVITY - FRAME"] = conn_rows
        tables["FRAME SECTION ASSIGNMENTS"] = sec_assign_rows

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

        return S2kWriter.write_tables(tables, target_path)
