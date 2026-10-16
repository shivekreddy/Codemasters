# ==============================================================================
# GCC Frequency Raw Data Export
# ==============================================================================
# Exports the latest frequency run to:
#     <RAW_DATA_ROOT>\Freq Raw Data\<study case name>.csv  (one per case)
#     <RAW_DATA_ROOT>\Freq Raw Data\export_manifest.json
# RAW_DATA_ROOT is set at the top of gcc_common.py (create that folder first).
#
# Cases without results are still exported, as zeros, so the script always
# runs. Point a ComPython object at this script and run it from PowerFactory.
# ==============================================================================

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcc_common as gcc

import powerfactory


# None -> the latest FREQ_* run folder; or e.g. "FREQ_2026-10-16_14-05-00"
RUN_TAG = None


def main():
    app = powerfactory.GetApplication()
    with gcc.pf_session(app):
        try:
            gcc.export_raw_results(app, "FREQ", __file__, run_tag=RUN_TAG)
        except Exception as exc:
            app.PrintError(f"Frequency raw data export aborted: {exc}")
            raise


main()
