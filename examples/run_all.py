"""
Runner script for all struct_link examples.
"""

import sys
from pathlib import Path


def main():
    print("=" * 75)
    print("  Running all struct_link examples")
    print("=" * 75)

    # 1. Ingest Complex Seismic SAP2000 model (Gravity, ELF, Modal, RSA)
    print("\n>>> [1/3] Running import_sap2000_seismic_frame.py...")
    import import_sap2000_seismic_frame

    import_sap2000_seismic_frame.run(show_plot=False)

    # 2. Export Complex Seismic Frame to SAP2000 (Gravity, ELF, Modal, RSA)
    print("\n>>> [2/3] Running export_sap2000_seismic_frame.py...")
    import export_sap2000_seismic_frame

    export_sap2000_seismic_frame.run(show_plot=False)

    # 3. Export Complex Seismic Frame to ETABS (Gravity, ELF, Modal, RSA)
    print("\n>>> [3/3] Running export_etabs_seismic_frame.py...")
    import export_etabs_seismic_frame

    export_etabs_seismic_frame.run(show_plot=False)

    print("\n" + "=" * 75)
    print("  All 3 struct_link examples executed successfully!")
    print("=" * 75)


if __name__ == "__main__":
    # Add examples dir to path if running directly
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
