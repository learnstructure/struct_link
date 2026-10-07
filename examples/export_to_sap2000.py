"""
Example: Exporting a struct_core Model to SAP2000 (.s2k).

Loads the struct_core model saved in output/frame_model.json (from import_sap2000_model.py)
and exports it to a native CSI SAP2000 text file (.s2k) using struct_link.write_model.
"""

from pathlib import Path
from typing import Tuple

from struct_core import Project, validate_structural_model
from struct_link import write_model


def run() -> Tuple[Project, Path]:
    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "frame_model.json"
    out_file = out_dir / "exported_3d_frame.s2k"

    # Ensure model is imported and saved first if running standalone
    if not json_path.exists():
        import sys
        examples_dir = str(Path(__file__).resolve().parent)
        if examples_dir not in sys.path:
            sys.path.insert(0, examples_dir)
        from import_sap2000_model import run as import_model
        import_model(show_plot=False)

    # 1. Load model in struct_core format
    project = Project.load_json(json_path)

    # 2. Validate structural model
    validate_structural_model(project.model)

    # 3. Export to SAP2000 (.s2k) format
    write_model(project, out_file)

    print(f"Successfully exported '{out_file.name}' to SAP2000 (.s2k):")
    print(f"  Nodes: {len(project.model.nodes)} | Elements: {len(project.model.elements)} | Supports: {len(project.model.supports)}")
    print(f"  File: {out_file.resolve()}")

    return project, out_file


if __name__ == "__main__":
    run()
