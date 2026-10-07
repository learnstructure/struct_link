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
    # (fem3d.model_from_core automatically calculates and applies self-weight
    # via struct_load for load cases with include_self_weight=True)
    structure = model_from_core(project)
    structure.solve()

    print(f"Successfully imported '{ref_path.name}' and converted to fem3d.Structure:")
    print(f"  Nodes: {len(structure.nodes)} | Elements: {len(structure.elements)}")
    print(f"  Saved struct_core model: {json_path.name}")
    
    results = Results(structure)
    reactions = results.reactions()
    print(f"Reactions:\n {reactions}")

    # 3. Visualize the model
    from fem3d import DrawStructure
    drawer = DrawStructure(structure, scale=1000)
    drawer.draw(show_deformed=True, show=show_plot)

    return project, structure


if __name__ == "__main__":
    run()
