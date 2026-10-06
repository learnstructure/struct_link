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

## Quick Start

### 1. Ingest a SAP2000 Model (.s2k)
```python
from struct_link import read_model

# Auto-detects SAP2000 from the .s2k file extension
model = read_model("my_building.s2k")
print(f"Imported {len(model.nodes)} nodes and {len(model.elements)} elements.")
```

### 2. Export a struct_core Model to ETABS (.e2k)
```python
from struct_link import write_model

write_model(project, "building_model.e2k")
```

### 3. Using Readers & Writers Explicitly
```python
from struct_link import Sap2000FileReader, EtabsFileWriter

reader = Sap2000FileReader()
model = reader.read_model("portal_frame.s2k")

writer = EtabsFileWriter()
writer.write_model(model, "portal_frame.e2k")
```

---

## Architecture

```
struct_link/
├── base.py                   # BaseModelReader, BaseModelWriter, BaseLiveSession
├── registry.py               # get_reader(), get_writer(), get_live_session()
└── backends/
    └── csi/
        ├── s2k_parser.py     # CSI table tokenizer and format serializer
        ├── sap2000/
        │   ├── file_reader.py # .s2k -> struct_core.StructuralModel
        │   ├── file_writer.py # struct_core.StructuralModel -> .s2k
        │   └── live_com.py    # CSI.SAP2000.API.SapModel live automation
        └── etabs/
            ├── file_reader.py # .e2k -> struct_core.StructuralModel
            ├── file_writer.py # struct_core.StructuralModel -> .e2k
            └── live_com.py    # CSI.ETABS.API.ETABSObject live automation
```

---

## License

MIT License.
