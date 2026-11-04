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
  - **Materials and sections as CSI defines them**: each frame section keeps its own `MATERIAL`; concrete materials with f'c become `ConcreteMaterial` (SAP2000 table `03B - CONCRETE DATA`, ETABS `FC`) and rebar with Fy / Fu `RebarMaterial` (`03E - REBAR DATA`, `FY` / `FU`); concrete sections keep their longitudinal and confinement bar materials and, for columns, the clear cover to the ties (`02 - CONCRETE COLUMN` `Cover`, ETABS `CONCRETESECTION ... COVER`). A beam's cover in CSI is measured to the bar centroid, so beams keep none. Nothing is invented: a property the file lacks stays missing, and the reader's `messages` list it (e.g. a concrete material without f'c). The writers emit the same tables and never write an f'c, density or modulus the model does not have.
  - Follows CSI defaults: frames are Timoshenko members, and a file without a mass source gets the CSI default (`MSSSRC1`, element self-mass only).
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
project = read_project("examples/output/etabs_sap_files/frame_ELF_RSA_Timoshenko.s2k")
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
| [`import_sap2000_seismic_frame.py`](examples/import_sap2000_seismic_frame.py) | Ingests complex SAP2000 model ([`frame_ELF_RSA_Timoshenko.s2k`](examples/output/etabs_sap_files/frame_ELF_RSA_Timoshenko.s2k)) with multiple gravity loads, ELF lateral seismic joint loads (`EQx`), dynamic mass source (`MSSSRC1`), and modal analysis case; verifies gravity reactions, ELF base shear (128.59 kN), 12 modal periods, and RSA base shear against SAP2000. | `python struct_link/examples/import_sap2000_seismic_frame.py` |
| [`export_sap2000_seismic_frame.py`](examples/export_sap2000_seismic_frame.py) | Reverse export: Constructs full 3D RC Space Frame with multiple gravity loads, dynamic mass source (`MSSSRC1`), ASCE 7-22 ELF seismic loads (`EQ_X`), modal (12 modes), and RSA (`RSx`); exports to native SAP2000 text file ([`exported_seismic_frame.s2k`](examples/output/etabs_sap_files/exported_seismic_frame.s2k)), re-imports via `read_project`, and verifies 100% roundtrip agreement for all analyses. | `python struct_link/examples/export_sap2000_seismic_frame.py` |
| [`export_etabs_seismic_frame.py`](examples/export_etabs_seismic_frame.py) | Reverse export to ETABS: Constructs full 3D RC Space Frame with gravity, dynamic mass source, ASCE 7-22 ELF, modal, and RSA; exports to native ETABS text file ([`exported_seismic_frame.e2k`](examples/output/etabs_sap_files/exported_seismic_frame.e2k)), re-imports via `read_project`, and verifies 100% roundtrip agreement. | `python struct_link/examples/export_etabs_seismic_frame.py` |
| [`run_all.py`](examples/run_all.py) | Executes all examples in sequence and verifies output. | `python struct_link/examples/run_all.py` |

### How to Import Exported Models into SAP2000:
1. Open **CSI SAP2000** (v14 through v27+).
2. Go to **File -> Import -> SAP2000 .s2k Text File...**.
3. Select `struct_link/examples/output/etabs_sap_files/exported_seismic_frame.s2k`.
4. SAP2000 automatically reconstructs the 3D grid, members, concrete sections, base restraints, load patterns, loads, dynamic mass sources, modal analysis cases, and response spectrum functions with units configured.

### How to Import Exported Models into ETABS:
1. Open **CSI ETABS** (v18 through v22+).
2. Go to **File -> Import -> ETABS .e2k Text File...**.
3. Select `struct_link/examples/output/etabs_sap_files/exported_seismic_frame.e2k`.
4. ETABS automatically reconstructs the 3D frame, concrete sections with Timoshenko shear areas, column local axes, base supports, load patterns (DEAD, SuperDead, LIVE, EQ_X), distributed beam loads, joint lateral forces, seismic mass source (`MSSSRC1`), 12-mode dynamic modal analysis case, and ASCE 7-22 response spectrum analysis case (`RSx`).

---

## Architecture

```
struct_link/
├── base.py                   # BaseModelReader, BaseModelWriter, BaseLiveSession
├── registry.py               # get_reader(), get_writer(), read_model(), read_project(), write_model()
├── examples/                 # Practical import, export, and cross-platform examples
│   ├── import_sap2000_seismic_frame.py
│   ├── export_sap2000_seismic_frame.py
│   ├── export_etabs_seismic_frame.py
│   ├── run_all.py
│   └── output/
│       └── etabs_sap_files/  # Verified commercial .s2k/.e2k models & export benchmarks
│           ├── frame_ELF_RSA_Timoshenko.s2k
│           ├── reference_etabs_model.e2k
│           ├── exported_seismic_frame.s2k
│           └── exported_seismic_frame.e2k
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

