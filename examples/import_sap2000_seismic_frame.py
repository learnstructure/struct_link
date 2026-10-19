"""
Example: Ingesting Complex SAP2000 Model with Gravity, ELF Seismic, and RSA.

Demonstrates complete interoperability for complex commercial models:
1. Ingests 'frame_ELF_RSA_Timoshenko.s2k' using `struct_link.read_project`.
2. Automatically parses:
   - Geometry, boundary conditions, and Timoshenko sections with shear deformation (AS2/AS3).
   - Multiple gravity load cases (DEAD, Live, SuperDead).
   - Auto-seismic joint loads from ASCE 7-22 ELF procedure (EQx).
   - Dynamic Mass Source definition (1.0*DEAD + 1.0*SuperDead + 0.25*Live).
   - Modal eigenvalue analysis case configuration.
3. Solves and verifies each analysis type directly against SAP2000 benchmarks:
   - Gravity vertical reactions: DEAD = 288.96 kN, Live = 280.00 kN, SuperDead = 700.00 kN.
   - ELF lateral seismic base shear: EQx = 128.59 kN.
   - Dynamic modal periods: Tn = [0.457663 s, 0.416672 s, 0.387409 s, ...].
   - Response Spectrum Analysis (RSA): exact continuous ASCE 7-22 (818.81 kN)
     and SAP2000 discrete table interpolation (846.38 kN).
"""

from pathlib import Path
import sys
from typing import Optional, Tuple

import pandas as pd
from fem3d import Structure, model_from_core, Results
from struct_core import Project, validate_structural_model
from struct_link import S2kParser, read_project
from struct_load import SeismicParameters


def run(show_plot: bool = False) -> Tuple[Project, Structure]:
    ref_path = (
        Path(__file__).resolve().parent
        / "output"
        / "etabs_sap_files"
        / "frame_ELF_RSA_Timoshenko.s2k"
    )
    if not ref_path.exists():
        raise FileNotFoundError(f"SAP2000 reference file not found at: {ref_path}")

    # -------------------------------------------------------------------------
    # 1. Ingest SAP2000 .s2k File into struct_core.Project
    # -------------------------------------------------------------------------
    project = read_project(ref_path)
    validate_structural_model(project.model)
    model = project.model

    print("=" * 85)
    print("STRUCT_LINK INTEROPERABILITY: IMPORT SAP2000 COMPLEX SEISMIC MODEL")
    print(f"File: {ref_path.name}")
    print("=" * 85)
    print(f"Model Summary: {len(model.nodes)} Nodes | {len(model.elements)} Elements | {len(model.supports)} Supports")
    print(f"Load Cases   : {[lc.id for lc in model.load_cases]}")
    print(f"Mass Source  : id='{model.mass_source.id}', include_elem={model.mass_source.include_element_mass}, factors={[f'{f.load_case_id}: {f.factor}' for f in model.mass_source.factors]}")

    # -------------------------------------------------------------------------
    # 2. Gravity Load Equilibrium Verification (DEAD, Live, SuperDead)
    # -------------------------------------------------------------------------
    print("\n" + "-" * 85)
    print("1. GRAVITY LOAD CASES & VERTICAL REACTIONS [vs SAP2000 BENCHMARKS]:")
    print("-" * 85)
    gravity_targets = {"DEAD": 288.96, "Live": 280.00, "SuperDead": 700.00}
    for lc_id, sap_val in gravity_targets.items():
        struct_g = model_from_core(project, load_case_id=lc_id, include_shear_deformation=True)
        struct_g.solve()
        reacs = Results(struct_g).reactions()
        sup_nodes = [nid for nid, n in struct_g.nodes.items() if n.is_supported()]
        fz_sum = float(reacs[reacs["node"].isin(sup_nodes)]["Fz"].sum())
        diff = abs(fz_sum - sap_val)
        print(f"  Case {lc_id:<11} : Fz = {fz_sum:10.2f} kN | SAP2000 = {sap_val:10.2f} kN | Diff = {diff:.4f} kN")

    # -------------------------------------------------------------------------
    # 3. Equivalent Lateral Force (ELF) Seismic Verification (EQx)
    # -------------------------------------------------------------------------
    print("\n" + "-" * 85)
    print("2. ASCE 7-22 EQUIVALENT LATERAL FORCE (ELF) ANALYSIS [EQx]:")
    print("-" * 85)
    struct_elf = model_from_core(project, load_case_id="EQx", include_shear_deformation=True)
    struct_elf.solve()
    reacs_elf = Results(struct_elf).reactions()
    sup_nodes = [nid for nid, n in struct_elf.nodes.items() if n.is_supported()]
    fx_sum = abs(float(reacs_elf[reacs_elf["node"].isin(sup_nodes)]["Fx"].sum()))
    sap_v_elf = 128.59
    print(f"  Base Shear Vbx (EQx) : {fx_sum:10.2f} kN | SAP2000 = {sap_v_elf:10.2f} kN | Diff = {abs(fx_sum - sap_v_elf):.4f} kN")

    # -------------------------------------------------------------------------
    # 4. Dynamic Modal Analysis (using automatically parsed MassSource)
    # -------------------------------------------------------------------------
    print("\n" + "-" * 85)
    print("3. DYNAMIC MODAL ANALYSIS (12 MODES) [vs SAP2000 BENCHMARKS]:")
    print("-" * 85)
    struct_dyn = model_from_core(project, include_shear_deformation=True)
    n_modes = model.analysis_cases[0].num_modes if model.analysis_cases else 12
    modal_res = struct_dyn.modal_analysis(num_modes=n_modes, lumped=True)

    w_seismic = struct_dyn.mass_source_result.total_seismic_weight
    m_dyn = struct_dyn.mass_source_result.total_mass
    print(f"  Effective Seismic Weight (W) : {w_seismic:10.2f} kN (SAP Target: 1058.96 kN)")
    print(f"  Dynamic Translational Mass (M): {m_dyn:10.2f} tonnes (SAP Target: 107.98 tonnes)")

    periods = modal_res["periods"]
    sap_periods = [0.457663, 0.416672, 0.387409, 0.301177, 0.229208, 0.149751]
    print(f"\n  {'Mode':<6} | {'Period Tn (s)':<14} | {'SAP2000 (s)':<14} | {'Diff (%)':<10}")
    print("  " + "-" * 55)
    for i in range(min(6, len(periods))):
        diff_pct = abs(periods[i] - sap_periods[i]) / sap_periods[i] * 100.0
        print(f"  {i+1:<6} | {periods[i]:<14.6f} | {sap_periods[i]:<14.6f} | {diff_pct:<10.4f}%")

    # -------------------------------------------------------------------------
    # 5. ASCE 7-22 Response Spectrum Analysis (RSx)
    # -------------------------------------------------------------------------
    print("\n" + "-" * 85)
    print("4. ASCE 7-22 RESPONSE SPECTRUM ANALYSIS (RSA) [RSx]:")
    print("-" * 85)
    tables = S2kParser.parse_file(ref_path)
    t_spec = tables.get("FUNCTION - RESPONSE SPECTRUM - ASCE 7-22")
    if t_spec and t_spec.rows:
        r0 = t_spec.rows[0]
        params = SeismicParameters(
            sds=float(r0.get("Ssd", 1.0)),
            sd1=float(r0.get("Sd1", 0.4)),
            tl=float(r0.get("TL", 8.0)),
            site_class=str(r0.get("SiteClass", "D")),
            damping_ratio=float(r0.get("FuncDamp", 0.05)),
        )
        t_pts = [float(r["Period"]) for r in t_spec.rows]
        sa_pts = [float(r["Accel"]) for r in t_spec.rows]

        # (A) Theoretical continuous formula: Sa = Sd1 / T
        rsa_cont = struct_dyn.response_spectrum_analysis(
            params=params,
            n_modes=n_modes,
            direction="x",
            scale_factor=9.80665,
            combination="CQC",
        )
        vb_cont = float(rsa_cont["combined_base_shear"][0])

        # (B) SAP2000 discrete piecewise linear table interpolation
        rsa_table = struct_dyn.response_spectrum_analysis(
            spectrum_curve=(t_pts, sa_pts),
            n_modes=n_modes,
            direction="x",
            scale_factor=9.8066,
            combination="CQC",
        )
        vb_table = float(rsa_table["combined_base_shear"][0])
        sap_vb_rsa = 846.384

        print(f"  ASCE 7-22 Continuous Equation (Exact Sa = Sd1/T): {vb_cont:10.2f} kN")
        print(f"  SAP2000 Table Linear Interp                     : {vb_table:10.2f} kN | SAP2000 = {sap_vb_rsa:10.2f} kN | Diff = {abs(vb_table - sap_vb_rsa)/sap_vb_rsa*100:.3f}%")
        print("  (Note: SAP2000's discrete function table with dt=0.2s yields Sa(0.458s)=0.904g vs exact 0.874g)")

    print("=" * 85)

    if show_plot:
        from fem3d import DrawStructure
        drawer = DrawStructure(struct_dyn, scale=100)
        drawer.draw(show=True)

    return project, struct_dyn


if __name__ == "__main__":
    run(show_plot=False)
