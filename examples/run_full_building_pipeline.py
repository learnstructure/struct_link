"""
Flagship End-to-End Multi-Story Building Engineering Pipeline.

Demonstrates the seamless integration of all Structural Engineering Ecosystem packages:
1. Ingest CSI SAP2000 model (struct_link) -> struct_core.Project
2. Building Code Load Generation & Combinations (struct_load) -> Preserves native SAP2000 gravity loads,
   computes exact seismic weight W, applies ASCE 7 ELF lateral (EQ_X, EQ_Y), and generates LRFD combinations
3. 3D Finite Element Analysis with Shear Deformations (fem3d) -> Envelopes & Member Demands
4. Reinforced Concrete Section & Column Design (aci318) -> Beam flexure/shear & Column Biaxial P-M-M
5. CAD Drawing & Detailing Generation (struct_draw) -> Framing Plans, Elevations, DXF Details

Usage:
    conda activate structeng
    python struct_link/examples/run_full_building_pipeline.py
"""

from pathlib import Path
from typing import Dict, Any

from struct_core import Project, validate_structural_model
from struct_link import read_project
from struct_load import LoadGenerator
from fem3d import model_from_core, Results
from aci318.codes.aci318_19 import ACI318_19
from aci318.materials.concrete import Concrete
from aci318.materials.steel import Steel
from aci318.sections.rectangle import RectangleSection
from aci318.members.beam import Beam
from aci318.members.column import Column
from aci318.loads.load_effects import LoadEffects
from struct_draw import (
    draw_framing_plan_from_core,
    draw_frame_elevation_from_core,
    save_dxf,
    BeamData,
    GeometrySection,
    Rebar,
    Stirrup,
    DrawingUnit,
    SCALE_1_4_IN,
)


def run_pipeline(
    output_dir: Path = None,
    recalculate_gravity: bool = False,
    verbose: bool = True,
) -> Dict[str, Any]:
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    ref_s2k = (
        Path(__file__).resolve().parent.parent
        / "references"
        / "sap2000"
        / "frame.s2k"
    )
    if not ref_s2k.exists():
        raise FileNotFoundError(f"Reference SAP2000 file not found at: {ref_s2k}")

    # =========================================================================
    # Step 1: Ingest Model into struct_core (struct_link)
    # =========================================================================
    if verbose:
        print("=" * 70)
        print("STEP 1: INGESTING CSI SAP2000 MODEL (.s2k -> struct_core)")
        print("=" * 70)

    project: Project = read_project(ref_s2k)
    validate_structural_model(project.model)

    if verbose:
        print(f"Loaded Project: {project.metadata.title or ref_s2k.name}")
        print(f"Nodes: {len(project.model.nodes)} | Elements: {len(project.model.elements)}")
        print(f"Length Unit: {project.metadata.units.length} | Force Unit: {project.metadata.units.force}")

    # =========================================================================
    # Step 2: Building Code Load Generation & Combinations (struct_load)
    # =========================================================================
    if verbose:
        print("\n" + "=" * 70)
        print("STEP 2: AUTOMATED LOAD GENERATION & ASCE 7 COMBINATIONS (struct_load)")
        print("=" * 70)

    load_gen = LoadGenerator()

    # 2a. Gravity Loads: Inspect existing loads vs recalculation
    existing_cases = {str(lc.id).upper(): lc for lc in project.model.load_cases}
    has_existing_gravity = "DEAD" in existing_cases and "LIVE" in existing_cases

    if has_existing_gravity and not recalculate_gravity:
        if verbose:
            print("Detected Existing Gravity Loads in Model (from SAP2000):")
            print(f"  • Load Case '{existing_cases['DEAD'].id}': {len(existing_cases['DEAD'].element_loads)} member loads + element self-weight")
            print(f"  • Load Case '{existing_cases['LIVE'].id}': {len(existing_cases['LIVE'].element_loads)} member loads")
            print("  -> Preserving native SAP2000 gravity loads (avoiding artificial duplication).")
    else:
        if verbose:
            if has_existing_gravity:
                print("Replacing existing SAP2000 gravity loads with synthesized ASCE 7 loads...")
            else:
                print("Generating initial gravity loads per ASCE 7 Table 4.3-1...")
        # Model coordinates are in inches; convert 12 ft tributary width and 20 psf SDL
        is_inch = str(project.metadata.units.length).lower() in ("in", "inch")
        trib_w = 144.0 if is_inch else 12.0
        sdl = (20.0 / 144.0) if is_inch else 1.0
        load_gen.apply_gravity_loads(
            project,
            occupancy="office",
            tributary_width=trib_w,
            superimposed_dead=sdl,
            include_self_weight=True,
        )

    # 2b. Lateral Seismic ELF (EQ_X and EQ_Y)
    # Automatically derives exact effective seismic weight W from the model's dead load
    seismic_cases, elf_res = load_gen.apply_lateral_seismic(
        project,
        sds=0.75,
        sd1=0.40,
        r_factor=8.0,
        direction="both",
    )

    # 2c. Factored Load Combinations per ASCE 7-16 / 22 with 3D orthogonal seismic
    combinations = load_gen.generate_combinations(
        project,
        design_method="LRFD",
        orthogonal_seismic=True,
    )

    if verbose:
        print(f"Active Model Load Cases: {[lc.id for lc in project.model.load_cases]}")
        print(f"Effective Seismic Weight W: {elf_res.total_weight:.2f} kips")
        print(f"Seismic Base Shear V: {elf_res.base_shear:.2f} kips | Period Ta: {elf_res.ta:.3f} s")
        print(f"Total Load Combinations: {len(combinations)} (including orthogonal seismic)")

    # =========================================================================
    # Step 3: 3D Finite Element Analysis with Shear Deformations (fem3d)
    # =========================================================================
    if verbose:
        print("\n" + "=" * 70)
        print("STEP 3: 3D FINITE ELEMENT ANALYSIS (fem3d)")
        print("=" * 70)

    # Convert to 3D space frame structure (Timoshenko beam theory with shear areas active)
    structure = model_from_core(project, include_shear_deformation=True)
    structure.solve()

    fem_results = Results(structure)
    reactions = fem_results.reactions()
    supported_nodes = [nid for nid, node in structure.nodes.items() if node.is_supported()]
    disp_reactions = reactions[reactions["node"].isin(supported_nodes)]

    total_vertical_reaction = reactions["Fz"].sum()

    if verbose:
        print(f"Solved fem3d.Structure successfully.")
        print(f"Total Vertical Base Reaction: {total_vertical_reaction:.2f} kips")
        print(f"Sample Reactions at Base Joints:")
        print(disp_reactions.head(4).to_string(index=False))

    # =========================================================================
    # Step 4: Concrete Section Design & Biaxial Column Interaction (aci318)
    # =========================================================================
    if verbose:
        print("\n" + "=" * 70)
        print("STEP 4: REINFORCED CONCRETE MEMBER DESIGN (aci318)")
        print("=" * 70)

    code = ACI318_19()
    conc = Concrete(fc=4.0)   # 4000 psi
    steel = Steel(fy=60.0)   # 60 ksi

    # 4a. Beam Design (12x24 in with 3 #8 tension bars)
    beam_sec = RectangleSection(b=12.0, h=24.0, concrete=conc)
    beam_sec.add_rebar_layer(num_bars=3, bar_size="#8", depth=21.5, steel=steel)
    beam_sec.add_rebar_layer(num_bars=2, bar_size="#8", depth=2.5, steel=steel)
    beam_member = Beam(section=beam_sec, code=code, member_id="B101")

    # Evaluate beam demand against section capacity
    beam_loads = LoadEffects(Mu=1800.0, Vu=35.0)  # Mu = 150 kip-ft = 1800 kip-in
    beam_design_res = beam_member.check(beam_loads)

    # 4b. Column Biaxial P-M-M Interaction Design (18x18 in column with 8 #8 bars)
    col_sec = RectangleSection(b=18.0, h=18.0, concrete=conc)
    col_sec.add_rebar_layer(num_bars=3, bar_size="#8", depth=2.5, steel=steel)
    col_sec.add_rebar_layer(num_bars=2, bar_size="#8", depth=9.0, steel=steel)
    col_sec.add_rebar_layer(num_bars=3, bar_size="#8", depth=15.5, steel=steel)
    col_member = Column(section=col_sec, code=code, member_id="C101")

    # Column under biaxial bending: Pu = 250 kips, Mu2 = 1200 kip-in, Mu3 = 800 kip-in
    col_loads = LoadEffects(Pu=250.0, Mu2=1200.0, Mu3=800.0, Vu2=25.0, Vu3=18.0)
    col_design_res = col_member.design(col_loads)

    if verbose:
        print(f"Beam B101 Design: Status = {beam_design_res.status}")
        print(f"  Flexure DCR: {beam_design_res.flexure.demand_capacity_ratio:.2f} | phi*Mn = {beam_design_res.flexure.phi_mn:.1f} kip-in")
        print(f"  Shear DCR:   {beam_design_res.shear.demand_capacity_ratio:.2f} | phi*Vn = {beam_design_res.shear.phi_vn:.1f} kips")
        print(f"Column C101 Biaxial Design: Status = {col_design_res.status}")
        print(f"  Biaxial DCR: {col_design_res.pm_interaction.demand_capacity_ratio:.2f}")
        print(f"  phi*Mnx: {col_design_res.pm_interaction.phi_mn_x:.1f} kip-in | phi*Mny: {col_design_res.pm_interaction.phi_mn_y:.1f} kip-in")
        print(f"  Method: {col_design_res.pm_interaction.method_used}")

    # =========================================================================
    # Step 5: Engineering Drafting & Drawing Generation (struct_draw)
    # =========================================================================
    if verbose:
        print("\n" + "=" * 70)
        print("STEP 5: STRUCTURAL DRAWINGS & DETAILING (struct_draw)")
        print("=" * 70)

    # 5a. General Floor Framing Plan DXF
    plan_dxf_path = output_dir / "framing_plan.dxf"
    draw_framing_plan_from_core(
        project,
        title="FIRST FLOOR FRAMING PLAN",
        unit=DrawingUnit.IN,
        scale=SCALE_1_4_IN,
        output_path=plan_dxf_path,
    )

    # 5b. Frame Elevation DXF
    elev_dxf_path = output_dir / "frame_elevation.dxf"
    draw_frame_elevation_from_core(
        project,
        title="FRAME ELEVATION - GRID 1",
        unit=DrawingUnit.IN,
        scale=SCALE_1_4_IN,
        output_path=elev_dxf_path,
    )

    # 5c. Reinforced Concrete Beam Section Detail DXF
    beam_dxf_path = output_dir / "beam_section_detail.dxf"
    beam_draw_data = BeamData(
        name="B101",
        section=GeometrySection(width_in=12.0, depth_in=24.0, cover_in=1.5),
        top_bars=[Rebar(mark="T1", size="#8", quantity=2)],
        bottom_bars=[Rebar(mark="B1", size="#8", quantity=3)],
        stirrups=Stirrup(size="#3", spacing_in=8.0, legs=2, width_in=12.0, height_in=24.0),
        length_in=288.0,
    )
    save_dxf(beam_draw_data, beam_dxf_path)

    if verbose:
        print(f"Generated DXF Drawings in '{output_dir}':")
        print(f"  1. {plan_dxf_path.name} (GA Floor Framing Plan)")
        print(f"  2. {elev_dxf_path.name} (Frame Elevation View)")
        print(f"  3. {beam_dxf_path.name} (Reinforced Concrete Beam Section Detail)")
        print("=" * 70)
        print("PIPELINE COMPLETED SUCCESSFULLY (0 Errors)")
        print("=" * 70)

    return {
        "project": project,
        "structure": structure,
        "beam_design": beam_design_res,
        "column_design": col_design_res,
        "total_reaction_z": total_vertical_reaction,
        "files_generated": [plan_dxf_path, elev_dxf_path, beam_dxf_path],
    }


if __name__ == "__main__":
    run_pipeline()
