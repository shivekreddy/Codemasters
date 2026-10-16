# ==============================================================================
# GCC FRT Raw Data Export
# ==============================================================================
# Exports the latest FRT run to:
#     <RAW_DATA_ROOT>\FRT Raw Data\<study case name>.csv  (one per case)
#     <RAW_DATA_ROOT>\FRT Raw Data\export_manifest.json
# RAW_DATA_ROOT is set at the top of gcc_common.py (create that folder first).
#
# Cases without results are still exported, as zeros, so the script always
# runs. Point a ComPython object at this script and run it from PowerFactory.
# ==============================================================================

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# PowerFactory keeps imported modules in memory for the whole session:
# drop any cached copy so gcc_common.py is always loaded fresh from THIS
# folder (not an older version, or a copy from another folder).
sys.modules.pop("gcc_common", None)
import gcc_common as gcc

import powerfactory


# None -> the latest FRT_* run folder; or e.g. "FRT_2026-10-16_13-50-06"
RUN_TAG = None


def main():
    app = powerfactory.GetApplication()
    with gcc.pf_session(app):
        try:
            gcc.export_raw_results(app, "FRT", __file__, run_tag=RUN_TAG)
        except Exception as exc:
            app.PrintError(f"FRT raw data export aborted: {exc}")
            raise


main()
