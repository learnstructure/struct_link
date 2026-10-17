# struct_link

`struct_link` is the **commercial CAE/FEM interoperability link** for the [Structural Engineering Ecosystem](../README.md).

It bridges commercial structural analysis packages (**CSI SAP2000**, **CSI ETABS**) with the ecosystem's open data model ([`struct_core`](../struct_core/README.md)).

> **Enables bidirectional data flow between commercial CAE software and the AI orchestration ecosystem.**

---

## Capabilities

- **File-Based Model Ingestion & Generation**:
  - **SAP2000**: Full support for `.s2k` and `.$2k` text formats.
  - **ETABS**: Full support for `.e2k` and `.$et` text formats.
  - Ingests nodes, frames, rectangular/circular/general sections, materials, 6-DOF supports, and nodal/distributed loads into `struct_core.StructuralModel`.
  - Serializes `struct_core.StructuralModel` or `Project` into valid, loadable `.s2k` and `.e2k` files.
- **Scalable Architecture**:
  - Abstract base classes (`BaseModelReader`, `BaseModelWriter`, `BaseLiveSession`) ensure that new backends (e.g. OpenSees, STAAD, SAFE) plug in without breaking existing workflows.
  - Built-in live COM/OAPI automation layer (`Sap2000LiveSession`, `EtabsLiveSession`) for running instances on Windows.
- **Agent Integration**:
  - Exposes deterministic tools to `struct_agent`: `import_from_sap2000`, `export_to_sap2000`, `import_from_etabs`, `export_to_etabs`.

---

## Installation

```bash
conda activate structeng
pip install -e struct_link/
```

To enable live Windows COM automation:
```bash
pip install -e "struct_link/[com]"
```

---

---

## Quick Start

### 1. Ingest a SAP2000 Model (.s2k)
```python
from struct_link import read_project

# Auto-detects SAP2000 from the .s2k file extension and extracts units
project = read_project("references/sap2000/frame.s2k")
model = project.model
print(f"Imported {len(model.nodes)} nodes, {len(model.elements)} elements, {len(model.supports)} supports.")
print(f"Canonical Units: {project.metadata.units} (Force={project.metadata.units.force}, Length={project.metadata.units.length})")
```

### 2. Export a struct_core Model to SAP2000 (.s2k)
```python
from struct_link import write_model

# Serializes with PROGRAM CONTROL, ACTIVE DOFS, COORDINATE SYSTEMS, RESTRAINTS, LOADS
write_model(project, "exported_building.s2k")
```

### 3. Cross-Platform CAE Conversion (SAP2000 -> ETABS)
```python
from struct_link import read_project, write_model

project = read_project("my_sap2000_model.s2k")
# Export directly to ETABS text format
write_model(project, "my_etabs_model.e2k")
```

---

## Examples & Workflows

Runnable example scripts are provided in [`examples/`](examples/):

| Script | Description | Run Command |
|---|---|---|
| [`import_sap2000_model.py`](examples/import_sap2000_model.py) | Ingests SAP2000 reference model ([`frame.s2k`](references/sap2000/frame.s2k)), preserves native self-weight and distributed loads, saves in `struct_core` format (`frame_model.json`), converts to `fem3d.Structure`, and solves for 3D equilibrium reactions with Timoshenko shear deformations considered. | `python struct_link/examples/import_sap2000_model.py` |
| [`import_sap2000_seismic_frame.py`](examples/import_sap2000_seismic_frame.py) | Ingests complex SAP2000 model ([`frame_ELF_RSA_Timoshenko.s2k`](references/sap2000/frame_ELF_RSA_Timoshenko.s2k)) with multiple gravity loads, ELF lateral seismic joint loads (`EQx`), dynamic mass source (`MSSSRC1`), and modal analysis case; verifies gravity reactions, ELF base shear (128.59 kN), 12 modal periods, and RSA base shear against SAP2000. | `python struct_link/examples/import_sap2000_seismic_frame.py` |
| [`export_sap2000_seismic_frame.py`](examples/export_sap2000_seismic_frame.py) | Reverse export: Constructs full 3D RC Space Frame with multiple gravity loads, dynamic mass source (`MSSSRC1`), ASCE 7-22 ELF seismic loads (`EQ_X`), modal (12 modes), and RSA (`RSx`); exports to native SAP2000 text file ([`exported_seismic_frame.s2k`](examples/output/exported_seismic_frame.s2k)), re-imports via `read_project`, and verifies 100% roundtrip agreement for all analyses. | `python struct_link/examples/export_sap2000_seismic_frame.py` |
| [`export_to_sap2000.py`](examples/export_to_sap2000.py) | Loads the `struct_core` model saved in `frame_model.json` and exports it to native CSI SAP2000 `.s2k` text format (`exported_3d_frame.s2k`). | `python struct_link/examples/export_to_sap2000.py` |
| [`roundtrip_and_conversion.py`](examples/roundtrip_and_conversion.py) | Demonstrates cross-platform interoperability by converting `frame.s2k` to ETABS `.e2k`, re-importing, and roundtripping back to `.s2k`. | `python struct_link/examples/roundtrip_and_conversion.py` |
| [`run_full_building_pipeline.py`](examples/run_full_building_pipeline.py) | Flagship end-to-end integration: Ingests `frame.s2k`, detects native gravity loads, generates ASCE 7 seismic ELF lateral loads & combinations, solves 3D FEA in `fem3d`, designs beams/columns in `aci318` (biaxial P-M-M), and generates DXF drawings in `struct_draw`. | `python struct_link/examples/run_full_building_pipeline.py` |
| [`run_all.py`](examples/run_all.py) | Executes all examples in sequence and verifies output. | `python struct_link/examples/run_all.py` |

### How to Import Exported Models into SAP2000:
1. Open **CSI SAP2000** (v14 through v27+).
2. Go to **File -> Import -> SAP2000 .s2k Text File...**.
3. Select `struct_link/examples/output/exported_seismic_frame.s2k` (or `exported_3d_frame.s2k`).
4. SAP2000 automatically reconstructs the 3D grid, members, concrete sections, base restraints, load patterns, loads, dynamic mass sources, modal analysis cases, and response spectrum functions with units configured.

---

## Architecture

```
struct_link/
├── base.py                   # BaseModelReader, BaseModelWriter, BaseLiveSession
├── registry.py               # get_reader(), get_writer(), read_model(), read_project(), write_model()
├── examples/                 # Practical import, export, and cross-platform examples
│   ├── import_sap2000_model.py
│   ├── import_sap2000_seismic_frame.py
│   ├── export_sap2000_seismic_frame.py
│   ├── export_to_sap2000.py
│   ├── roundtrip_and_conversion.py
│   └── run_all.py
├── references/               # Real-world CAE reference files from commercial software
│   └── sap2000/
│       └── frame.s2k         # 36-joint, 58-frame 3D model exported from SAP2000 v27
└── backends/
    └── csi/
        ├── s2k_parser.py     # CSI table tokenizer and format serializer (handles continuations & quotes)
        ├── sap2000/
        │   ├── file_reader.py # .s2k -> struct_core.StructuralModel / Project
        │   ├── file_writer.py # struct_core.StructuralModel -> .s2k
        │   └── live_com.py    # CSI.SAP2000.API.SapModel live automation
        └── etabs/
            ├── file_reader.py # .e2k -> struct_core.StructuralModel / Project
            ├── file_writer.py # struct_core.StructuralModel -> .e2k
            └── live_com.py    # CSI.ETABS.API.ETABSObject live automation
```

---

## License

MIT License.

