"""
Example: Ingesting a SAP2000 .s2k Model and Converting to fem3d.Structure.

Imports the reference SAP2000 text file (references/sap2000/frame.s2k) into
the ecosystem data model (struct_core.Project), validates it, and converts it
into a solvable 3D FEA model (fem3d.Structure).
"""

from pathlib import Path
from typing import Tuple

from fem3d import Structure, model_from_core, Results
from struct_core import Project, validate_structural_model
from struct_link import read_project


def run(show_plot: bool = True) -> Tuple[Project, Structure]:
    ref_path = (
        Path(__file__).resolve().parent.parent
        / "references"
        / "sap2000"
        / "frame.s2k"
    )

    if not ref_path.exists():
        raise FileNotFoundError(f"SAP2000 reference file not found at: {ref_path}")

    # 1. Ingest SAP2000 .s2k file into struct_core.Project
    project = read_project(ref_path)
    validate_structural_model(project.model)

    # 2. Save imported model in struct_core format (.json)
    # (Preserves native SelfWtMult=1 on DEAD case and original distributed loads)
    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "frame_model.json"
    project.save_json(json_path)

    # 3. Convert to fem3d.Structure
    # (By setting include_shear_deformation=True, Timoshenko beam theory is used,
    # matching SAP2000's default behavior where section shear areas AS2/AS3 are active)
    structure = model_from_core(project, include_shear_deformation=True)
    structure.solve()

    print(f"Successfully imported '{ref_path.name}' and converted to fem3d.Structure:")
    print(f"  Nodes: {len(structure.nodes)} | Elements: {len(structure.elements)}")
    print(f"  Saved struct_core model: {json_path.name}")
    print(f"  Shear deformations: Included (Timoshenko beam theory, AS2/AS3 active)")

    results = Results(structure)
    reactions = results.reactions()
    # Filter to supported joints for clean, readable output
    supported_nodes = [nid for nid, node in structure.nodes.items() if node.is_supported()]
    disp_reactions = reactions[reactions["node"].isin(supported_nodes)].copy()
    print(f"\nSupport Reactions (with shear deformations considered):")
    print(disp_reactions.to_string(index=False))

    print(f"\nEquilibrium Check:")
    print(f"  Sum Fx: {reactions['Fx'].sum():.6e} kips")
    print(f"  Sum Fy: {reactions['Fy'].sum():.6e} kips")
    print(f"  Sum Fz: {reactions['Fz'].sum():.4f} kips (Total vertical load)")

    # Optional: Compare with Euler-Bernoulli (no shear deformation)
    struct_eb = model_from_core(project, include_shear_deformation=False)
    struct_eb.solve()
    reac_eb = Results(struct_eb).reactions()
    disp_eb = reac_eb[reac_eb["node"].isin(supported_nodes)].copy()

    diff_fz = (disp_reactions["Fz"].values - disp_eb["Fz"].values)
    max_diff_idx = abs(diff_fz).argmax()
    node_max_diff = disp_reactions["node"].iloc[max_diff_idx]
    first_node = disp_reactions["node"].iloc[0]
    fz_timo_0 = disp_reactions.loc[disp_reactions["node"] == first_node, "Fz"].values[0]
    fz_eb_0 = disp_eb.loc[disp_eb["node"] == first_node, "Fz"].values[0]
    print(f"\nShear Deformation Effect vs Euler-Bernoulli:")
    print(f"  Corner Node {first_node} Fz: {fz_timo_0:.4f} kips (Timoshenko) vs {fz_eb_0:.4f} kips (Euler-Bernoulli)")
    print(f"  Max absolute reaction shift: {abs(diff_fz[max_diff_idx]):.4f} kips at Node {node_max_diff}")

    # 4. Visualize the model
    from fem3d import DrawStructure
    drawer = DrawStructure(structure, scale=1000)
    drawer.draw(show_deformed=True, show=show_plot)

    return project, structure


if __name__ == "__main__":
    run()
