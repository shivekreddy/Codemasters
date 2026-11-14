# ==============================================================================
# GCC FRT: Export Selected Cases
# ==============================================================================
# Same export as frt_3_export_raw_data.py (identical files and layout), but
# only for the study cases listed below and into a folder you choose: for
# re-running two or three cases and keeping their results apart.
#
# Output:  <OUTPUT_DIR>\<FRT run folder>\
#            FRT 1PH 0.6 Qmax\POC.csv, GU Terminal 1.csv, GU Terminal 2.csv
#            FRT 2PH 0.85 Qmax\...
#            export_manifest.json   (lists the cases in the folder)
#
# Exporting again into the same folder adds or replaces cases; cases exported
# earlier stay listed in the manifest. The folder is created if missing.
#
# Check the cases afterwards with the FRT check from the command line:
#     python frt_5_compliance_check.py "<OUTPUT_DIR>\<FRT run folder>" --out <file>.xlsx
#
# Usage: set CASES and OUTPUT_DIR below, point a ComPython object at this
# script and run it from PowerFactory.
# Dependencies: PyYAML, gcc_common.py and the project config in Working
# Scripts (one folder up).
# ==============================================================================

import os
import sys

import powerfactory

# gcc_common.py and the project config live one folder up (Working Scripts)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.pop("gcc_common", None)      # always load gcc_common.py fresh
import gcc_common as gcc                 # noqa: E402


# ------------------------------------------------------------------------------
# SETTINGS
# ------------------------------------------------------------------------------
# Cases to export: the PowerFactory study case name or the report name,
# upper/lower case does not matter, * and ? are wildcards.
#   "FRT 1PH 0.6 Qmax"   or   "1P-60U-Qmax"
#   "FRT 2PH * Qmax"     every 2-phase Qmax case
CASES = [
    "FRT 1PH 0.6 Qmax",
    "FRT 2PH 0.85 Qmax",
]

# Folder to write to (created if missing); the run folder name is added below
# it. None -> <paths.root>\FRT Selected Cases (paths.root from the project config)
OUTPUT_DIR = None

# Run folder in Study Cases to take the cases from.
# None -> paths.frt_run from the project config, else the latest FRT_* run.
RUN_FOLDER = None


# ==============================================================================

def main():
    app = powerfactory.GetApplication()
    if not CASES:
        app.PrintError("CASES is empty: list the study cases to export.")
        return
    out = OUTPUT_DIR
    if not out:
        cfg = gcc.load_yaml(gcc.resolve_config_path(__file__))
        out = os.path.join(gcc.results_root(cfg), "FRT Selected Cases")
    gcc.export_frt_raw(app, __file__, output_dir=out, case_patterns=CASES,
                       run_folder=RUN_FOLDER)


main()
