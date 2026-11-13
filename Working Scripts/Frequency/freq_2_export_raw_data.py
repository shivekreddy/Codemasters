# ==============================================================================
# GCC Frequency Raw Data Export
# ==============================================================================
# Exports the latest frequency run (or paths.freq_run from the project config)
# to:
#     <paths.root>\Freq Raw Data\<FREQ run folder>\<study case name>.csv
#     <paths.root>\Freq Raw Data\<FREQ run folder>\export_manifest.json
# paths.root is set in the project config next to this script. Nothing to
# set in this script.
#
# Cases without results are still exported, as zeros, so the script always
# runs. Point a ComPython object at this script and run it from PowerFactory.
# ==============================================================================

import sys
import os

# gcc_common.py and the project config live one folder up (Working Scripts)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# PowerFactory keeps imported modules in memory for the whole session:
# drop any cached copy so gcc_common.py is always loaded fresh from THIS
# folder (not an older version, or a copy from another folder).
sys.modules.pop("gcc_common", None)
import gcc_common as gcc

import powerfactory


def main():
    app = powerfactory.GetApplication()
    with gcc.pf_session(app):
        try:
            gcc.export_raw_results(app, "FREQ", __file__)
        except Exception as exc:
            app.PrintError(f"Frequency raw data export aborted: {exc}")
            raise


main()
