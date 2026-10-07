"""
Example: Cross-Platform Interoperability (SAP2000 -> ETABS).

Ingests a SAP2000 model (.s2k), validates it, and exports it directly
to an ETABS text file (.e2k) to demonstrate CAE format conversion.
"""

from pathlib import Path
from typing import Tuple

from struct_core import Project, StructuralModel, validate_structural_model
from struct_link import read_model, read_project, write_model


def run() -> Tuple[Project, StructuralModel]:
    ref_path = (
        Path(__file__).resolve().parent.parent
        / "references"
        / "sap2000"
        / "frame.s2k"
    )
    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    etabs_out = out_dir / "frame_converted.e2k"

    if not ref_path.exists():
        raise FileNotFoundError(f"Reference file not found: {ref_path}")

    # 1. Ingest SAP2000 model (.s2k)
    project = read_project(ref_path)
    validate_structural_model(project.model)

    # 2. Export to ETABS format (.e2k)
    write_model(project, etabs_out)

    # 3. Verify re-import from ETABS
    etabs_model = read_model(etabs_out)
    validate_structural_model(etabs_model)

    print(f"Successfully converted '{ref_path.name}' (SAP2000) -> '{etabs_out.name}' (ETABS):")
    print(f"  Nodes: {len(etabs_model.nodes)} | Elements: {len(etabs_model.elements)} | Supports: {len(etabs_model.supports)}")
    print(f"  File: {etabs_out.resolve()}")

    return project, etabs_model


if __name__ == "__main__":
    run()
