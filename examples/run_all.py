"""
Runner script for all struct_link examples.
"""

import sys
from pathlib import Path


def main():
    print("=" * 75)
    print("  Running all struct_link examples")
    print("=" * 75)

    # 1. Ingest SAP2000 model
    print("\n>>> [1/4] Running import_sap2000_model.py...")
    import import_sap2000_model

    import_sap2000_model.run(show_plot=False)

    # 2. Export 3D Frame to SAP2000
    print("\n>>> [2/4] Running export_to_sap2000.py...")
    import export_to_sap2000

    export_to_sap2000.run()

    # 3. Cross-platform roundtrip & conversion
    print("\n>>> [3/4] Running roundtrip_and_conversion.py...")
    import roundtrip_and_conversion

    roundtrip_and_conversion.run()

    # 4. Ingest Complex Seismic SAP2000 model (Gravity, ELF, Modal, RSA)
    print("\n>>> [4/5] Running import_sap2000_seismic_frame.py...")
    import import_sap2000_seismic_frame

    import_sap2000_seismic_frame.run(show_plot=False)

    # 5. Export Complex Seismic Frame to SAP2000 (Gravity, ELF, Modal, RSA)
    print("\n>>> [5/5] Running export_sap2000_seismic_frame.py...")
    import export_sap2000_seismic_frame

    export_sap2000_seismic_frame.run(show_plot=False)

    print("\n" + "=" * 75)
    print("  All 5 struct_link examples executed successfully!")
    print("=" * 75)


if __name__ == "__main__":
    # Add examples dir to path if running directly
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
