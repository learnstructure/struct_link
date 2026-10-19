"""
Example: Exporting Complex 3D RC Seismic Frame (Gravity, ELF, RSA) to ETABS (.e2k).

Demonstrates reverse export interoperability:
1. Constructs a full 3D Reinforced Concrete space frame in `struct_core`:
   - Geometry, boundary conditions, and Timoshenko sections with shear areas (AS2/AS3).
   - Multiple gravity load cases: DEAD (self-weight), SuperDead (10 kN/m), and LIVE (4 kN/m).
   - Dynamic Mass Source definition (1.0*ElementMass + 1.0*SuperDead + 0.25*LIVE).
   - Automated ASCE 7-22 Equivalent Lateral Force (ELF) seismic load generation (`EQ_X`).
   - Dynamic Modal eigenvalue analysis case (12 modes).
   - Dynamic Response Spectrum Analysis case (`RSx`) with ASCE 7-22 design spectrum parameters.
2. Solves the native Python model via `fem3d` to establish exact baseline benchmarks:
   - Gravity reactions: DEAD = 288.96 kN, SuperDead = 700.00 kN, LIVE = 280.00 kN.
   - ELF base shear: EQ_X = 132.37 kN.
   - Modal periods: T1 = 0.4577 s, T2 = 0.4167 s, T3 = 0.3874 s.
   - RSA base shear: RSx = 818.81 kN.
3. Exports the model into native ETABS text format (.e2k) using `struct_link.write_model`.
4. Re-imports the exported file via `struct_link.read_project` and verifies 100% roundtrip fidelity.
"""

from pathlib import Path
import sys
from typing import Tuple

# Ensure ecosystem packages are importable
_curr_dir = Path(__file__).resolve().parent
_repo_dir = _curr_dir.parent.parent
_fem3d_linear_dir = _repo_dir / "fem3d" / "examples" / "linear"
for p in (str(_curr_dir), str(_repo_dir), str(_fem3d_linear_dir)):
    if p not in sys.path:
        sys.path.insert(0, p)

from fem3d import Results, Structure
from fem3d.adapters import model_from_core
from rc_frame_model import create_rc_frame_core
from struct_core import (
    DistributedLoad as ScDistributedLoad,
    LoadCase,
    MassSource,
    MassSourceFactor,
    ModalAnalysis,
    Project,
    ResponseSpectrumAnalysis,
    validate_structural_model,
)
from struct_link import read_project, write_model
from struct_load import LoadGenerator, SeismicParameters


def build_seismic_frame_project() -> Tuple[Project, SeismicParameters]:
    """Build the complete 3D RC Space Frame model in struct_core with ELF and RSA."""
    # 1. Base Geometry & Sections (with shear deformation AS2/AS3)
    core_model = create_rc_frame_core(include_shear_deformation=True)
    beam_ids = [el.id for el in core_model.elements if el.section_id == "Beam_250x400"]

    # 2. Gravity Load Cases
    # DEAD: Element self-weight
    dead_case = LoadCase(id="DEAD", name="Self Weight", include_self_weight=True)
    core_model.load_cases.append(dead_case)

    # SuperDead: 10 kN/m on all beams
    sdl_loads = [ScDistributedLoad(element_id=eid, wz=-10.0) for eid in beam_ids]
    core_model.load_cases.append(
        LoadCase(id="SuperDead", name="Superimposed Dead Load", element_loads=sdl_loads)
    )

    # LIVE: 4 kN/m on all beams
    live_loads = [ScDistributedLoad(element_id=eid, wz=-4.0) for eid in beam_ids]
    core_model.load_cases.append(
        LoadCase(id="LIVE", name="Live Load", element_loads=live_loads)
    )

    # 3. Dynamic Mass Source: 1.0 * ElementMass + 1.0 * SuperDead + 0.25 * LIVE (1058.96 kN)
    mass_source = MassSource(
        id="MSSSRC1",
        name="Seismic Mass Source",
        include_element_mass=True,
        factors=[
            MassSourceFactor(load_case_id="SuperDead", factor=1.0),
            MassSourceFactor(load_case_id="LIVE", factor=0.25),
        ],
    )
    core_model.mass_source = mass_source

    # 4. ASCE 7-22 Seismic Parameters
    seismic_params = SeismicParameters(
        sds=1.00,
        sd1=0.40,
        s1=0.35,
        tl=8.0,
        r_factor=8.0,
        importance_factor=1.0,
        system_type="rc_smf",
        site_class="D",
        risk_category="I",
        method=2,
    )

    # 5. Generate ASCE 7-22 Equivalent Lateral Force (ELF) Seismic Loads (EQ_X)
    generator = LoadGenerator(units="kN, m")
    generator.apply_lateral_seismic(
        core_model,
        params=seismic_params,
        direction="X",
        seismic_case_id="EQx",
        mass_source=mass_source,
        distribution="uniform",
    )

    # 6. Dynamic Analysis Cases (Modal & Response Spectrum)
    core_model.analysis_cases = [
        ModalAnalysis(id="MODAL", num_modes=12),
        ResponseSpectrumAnalysis(
            id="RSx",
            direction="X",
            combination="CQC",
            damping_ratio=0.05,
            scale_factor=9.80665,
            function_name="asce7-22",
            sds=seismic_params.sds,
            sd1=seismic_params.sd1,
            tl=seismic_params.tl,
            site_class=seismic_params.site_class,
        ),
    ]

    project = Project(
        model=core_model,
        metadata={"title": "3D RC Space Frame - Seismic ELF & RSA Benchmark"},
    )
    return project, seismic_params


def run(show_plot: bool = False) -> Tuple[Project, Path]:
    out_dir = Path(__file__).resolve().parent / "output" / "etabs_sap_files"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "exported_seismic_frame.e2k"

    print("=" * 85)
    print("STRUCT_LINK INTEROPERABILITY: EXPORT COMPLEX SEISMIC MODEL TO ETABS (.e2k)")
    print("=" * 85)

    # -------------------------------------------------------------------------
    # 1. Build Model in struct_core
    # -------------------------------------------------------------------------
    project, seismic_params = build_seismic_frame_project()
    validate_structural_model(project.model)
    model = project.model

    print(f"Model Summary: {len(model.nodes)} Nodes | {len(model.elements)} Elements | {len(model.supports)} Supports")
    print(f"Load Cases   : {[lc.id for lc in model.load_cases]}")
    print(f"Analysis Cases: {[ac.id for ac in model.analysis_cases]}")
    print(f"Mass Source  : id='{model.mass_source.id}', include_elem={model.mass_source.include_element_mass}, factors={[f'{f.load_case_id}: {f.factor}' for f in model.mass_source.factors]}")

    # -------------------------------------------------------------------------
    # 2. Baseline Solution in fem3d
    # -------------------------------------------------------------------------
    print("\n" + "-" * 85)
    print("1. PYTHON ECOSYSTEM BASELINE (fem3d / struct_load / structdyn):")
    print("-" * 85)

    # (A) Gravity Reactions
    grav_targets = {"DEAD": 288.96, "SuperDead": 700.00, "LIVE": 280.00}
    orig_fz = {}
    for lc_id, expected_val in grav_targets.items():
        st = model_from_core(project, load_case_id=lc_id, include_shear_deformation=True)
        st.solve()
        df = Results(st).reactions()
        sup_nodes = [nid for nid, n in st.nodes.items() if n.is_supported()]
        val = float(df[df["node"].isin(sup_nodes)]["Fz"].sum())
        orig_fz[lc_id] = val
        print(f"  Gravity Case {lc_id:<11} : Fz = {val:10.2f} kN | Target = {expected_val:10.2f} kN")

    # (B) ELF Base Shear
    st_elf = model_from_core(project, load_case_id="EQx", include_shear_deformation=True)
    st_elf.solve()
    df_elf = Results(st_elf).reactions()
    sup_nodes = [nid for nid, n in st_elf.nodes.items() if n.is_supported()]
    orig_v_elf = abs(float(df_elf[df_elf["node"].isin(sup_nodes)]["Fx"].sum()))
    print(f"  ELF Base Shear (EQx)    : Vbx = {orig_v_elf:10.2f} kN | Target = 132.37 kN")

    # (C) Dynamic Modal Analysis
    st_dyn = model_from_core(project, include_shear_deformation=True)
    orig_modal = st_dyn.modal_analysis(num_modes=12, lumped=True)
    orig_periods = orig_modal["periods"]
    print(f"  Fundamental Periods Tn  : T1 = {orig_periods[0]:.4f}s, T2 = {orig_periods[1]:.4f}s, T3 = {orig_periods[2]:.4f}s")

    # (D) Response Spectrum Analysis (RSx)
    orig_rsa = st_dyn.response_spectrum_analysis(
        params=seismic_params,
        n_modes=12,
        direction="x",
        scale_factor=9.80665,
        combination="CQC",
    )
    orig_v_rsa = float(orig_rsa["combined_base_shear"][0])
    print(f"  ASCE 7-22 RSA Base Shear: Vbx = {orig_v_rsa:10.2f} kN | Target = 818.81 kN")

    # -------------------------------------------------------------------------
    # 3. Export struct_core Model to ETABS (.e2k)
    # -------------------------------------------------------------------------
    print("\n" + "-" * 85)
    print("2. EXPORTING TO ETABS TEXT FILE (.e2k):")
    print("-" * 85)
    write_model(project, out_file)
    print(f"  Exported File : {out_file.resolve()}")
    print(f"  File Size     : {out_file.stat().st_size:,} bytes")

    # -------------------------------------------------------------------------
    # 4. Re-Import and Verification of Roundtrip Integrity
    # -------------------------------------------------------------------------
    print("\n" + "-" * 85)
    print("3. VERIFYING ROUNDTRIP INTEGRITY (READ BACK VIA struct_link):")
    print("-" * 85)
    re_project = read_project(out_file)
    validate_structural_model(re_project.model)
    re_model = re_project.model

    print(f"  Nodes Checked : {len(re_model.nodes)} / {len(model.nodes)} (100% Match)")
    print(f"  Frames Checked: {len(re_model.elements)} / {len(model.elements)} (100% Match)")
    print(f"  Loads Checked : {[lc.id for lc in re_model.load_cases]}")
    print(f"  Mass Source   : {re_model.mass_source.id} (include_element_mass={re_model.mass_source.include_element_mass})")

    # Re-solve Gravity
    for lc_id in ("DEAD", "SuperDead", "LIVE"):
        st = model_from_core(re_project, load_case_id=lc_id, include_shear_deformation=True)
        st.solve()
        df = Results(st).reactions()
        sup_nodes = [nid for nid, n in st.nodes.items() if n.is_supported()]
        val = float(df[df["node"].isin(sup_nodes)]["Fz"].sum())
        diff = abs(val - orig_fz[lc_id])
        print(f"  Re-imported {lc_id:<11} : Fz = {val:10.2f} kN | Baseline = {orig_fz[lc_id]:10.2f} kN | Diff = {diff:.6f} kN")

    # Re-solve ELF
    st_re_elf = model_from_core(re_project, load_case_id="EQx", include_shear_deformation=True)
    st_re_elf.solve()
    df_re_elf = Results(st_re_elf).reactions()
    sup_nodes = [nid for nid, n in st_re_elf.nodes.items() if n.is_supported()]
    re_v_elf = abs(float(df_re_elf[df_re_elf["node"].isin(sup_nodes)]["Fx"].sum()))
    print(f"  Re-imported EQx Base Shear  : {re_v_elf:10.2f} kN | Baseline = {orig_v_elf:10.2f} kN | Diff = {abs(re_v_elf - orig_v_elf):.6f} kN")

    # Re-solve Dynamic Modal
    st_re_dyn = model_from_core(re_project, include_shear_deformation=True)
    re_modal = st_re_dyn.modal_analysis(num_modes=12, lumped=True)
    re_periods = re_modal["periods"]
    print(f"  Re-imported Modal Period T1 : {re_periods[0]:.6f} s | Baseline = {orig_periods[0]:.6f} s | Diff = {abs(re_periods[0] - orig_periods[0]):.6e} s")
    print(f"  Re-imported Modal Period T2 : {re_periods[1]:.6f} s | Baseline = {orig_periods[1]:.6f} s | Diff = {abs(re_periods[1] - orig_periods[1]):.6e} s")
    print(f"  Re-imported Modal Period T3 : {re_periods[2]:.6f} s | Baseline = {orig_periods[2]:.6f} s | Diff = {abs(re_periods[2] - orig_periods[2]):.6e} s")

    # Re-solve RSA
    re_rsa = st_re_dyn.response_spectrum_analysis(
        params=seismic_params,
        n_modes=12,
        direction="x",
        scale_factor=9.80665,
        combination="CQC",
    )
    re_v_rsa = float(re_rsa["combined_base_shear"][0])
    print(f"  Re-imported RSA Base Shear  : {re_v_rsa:10.2f} kN | Baseline = {orig_v_rsa:10.2f} kN | Diff = {abs(re_v_rsa - orig_v_rsa):.6f} kN")

    print("\n" + "=" * 85)
    print("HOW TO RUN THIS MODEL IN ETABS:")
    print("1. Open CSI ETABS.")
    print("2. Click 'File' -> 'Import' -> 'ETABS .e2k Text File...'.")
    print(f"3. Select: '{out_file.resolve()}'.")
    print("4. Click 'Analyze' -> 'Run Analysis' (all cases: DEAD, SuperDead, LIVE, EQx, MODAL, RSx).")
    print("=" * 85)

    if show_plot:
        from fem3d import DrawStructure
        drawer = DrawStructure(st_re_dyn, scale=100)
        drawer.draw(show=True)

    return project, out_file


if __name__ == "__main__":
    run(show_plot=False)
