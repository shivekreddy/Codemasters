# ==============================================================================
# GCC Frequency: Export Selected Cases
# ==============================================================================
# Same export as freq_2_export_raw_data.py (identical files), but only for the
# cases switched on below and into a folder of its own: for running part of
# the frequency cases in one model and the rest in another.
#
# Output:  <OUTPUT_DIR>\<FREQ run folder>\
#            FSM_Step.csv   + FSM_Step.json     (one pair per case)
#            ...
#            export_manifest.json
#
# Each <case>.json holds that case's event times and setpoints. To combine
# models, copy each case's .csv AND .json into the run folder that freq_4
# checks (<paths.root>\Freq Raw Data\<run>). freq_4 reads the .json files and
# they take priority over that folder's export_manifest.json, so the copied
# cases are evaluated with their own event times.
#
# Exporting again into the same folder adds or replaces cases.
#
# Usage: set the switches below (1 = export, 0 = skip), point a ComPython
# object at this script and run it from PowerFactory, in each model.
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
# SETTINGS: 1 = export, 0 = skip
# ------------------------------------------------------------------------------
CASES = {
    "FSM Ramp":     1,
    "FSM Step":     1,
    "LFSMO Ramp":   0,
    "LFSMO Step":   0,
    "LFSMU Ramp":   0,
    "LFSMU Step":   0,
    "FSMINS Ramp":  0,     # FSM insensitivity (only if the grid code has it)
    "FSMINS Step":  0,
}

# Folder to write to (created if missing); the run folder name is added below
# it. None -> <paths.root>\Freq Selected Cases
OUTPUT_DIR = None

# Run folder in Study Cases of THIS model.
# None -> paths.freq_run from the project config, else the latest FREQ_* run.
RUN_FOLDER = None


# ==============================================================================

def main():
    app = powerfactory.GetApplication()
    selected = [name for name, on in CASES.items() if on]
    if not selected:
        app.PrintError("No case switched on: set at least one case to 1.")
        return
    cfg = gcc.load_yaml(gcc.resolve_config_path(__file__))
    out = OUTPUT_DIR or os.path.join(gcc.results_root(cfg), "Freq Selected Cases")
    with gcc.pf_session(app):
        try:
            gcc.export_raw_results(app, "FREQ", __file__, run_tag=RUN_FOLDER,
                                   case_names=selected, output_dir=out)
        except Exception as exc:
            app.PrintError(f"Frequency selected-cases export aborted: {exc}")
            raise
    app.PrintPlain("\n  Copy each case's .csv AND .json into the Freq Raw Data run "
                   "folder, then run freq_4.")


main()
