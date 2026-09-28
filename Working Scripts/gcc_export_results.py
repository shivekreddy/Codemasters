# ==============================================================================
# GCC Results Export Script — FRT and Frequency (standalone)
# ==============================================================================
# Exports simulation results from completed runs to ONE CSV FILE PER STUDY
# CASE. Run this after the simulations have finished (any execution mode).
# It creates and runs nothing — it only reads result files.
#
# RUN SELECTION (in priority order):
#   1. RUN_TAG set below          -> that folder, no dialog (automation path)
#   2. exactly one {STUDY}_* run  -> exported, no dialog
#   3. several runs               -> PowerFactory selection dialog opens.
#      MULTI-SELECT is supported: Ctrl-click several runs and each one is
#      exported into its own subfolder. Cancel aborts cleanly.
#
# The run's JSON manifest (written by the simulation script into
# results.output_dir) is the authority for what gets exported:
#   * the case list and their solved/failed status come from the manifest,
#   * CSV file names are derived from the case names
#     (gcc_common.case_csv_filename: "FRT 3PH 0.05 Q0" -> FRT_3PH_0.05_Q0.csv),
#   * an EXPORT MANIFEST is written alongside the CSVs mapping every case to
#     its file, so the evaluation tool never has to parse file names.
#
# Output layout (inside results.output_dir from project_config.yaml):
#
#   {output_dir}\
#     FRT_2026-07-09_14-30-54\          <- one folder per exported run
#       FRT_3PH_0.05_Q0.csv             <- one CSV per case
#       ...
#       export_manifest.json            <- case -> file mapping
#
# CSV format per file:
#   time_s, <one column per gcc_common.RECORD_VARS_FRT / _FREQ entry>,
#   headers are the friendly labels ("POC Pos-Seq Voltage [pu]").
#   NOTE: the time base is NON-UNIFORM (iopt_adapt=1) — the evaluation tool
#   must interpolate on time_s, never index by row.
#
# If ONE selected run fails (missing manifest, missing cases), the others
# still export; the summary reports per-run status.
#
# Usage:
#   1. Set STUDY ("FRT" or "FREQ") and check CONFIG_FILE below
#   2. Point a ComPython object at this script and run from PowerFactory
#
# Dependencies: PyYAML, gcc_common.py v2.0+ in the same folder as this script.
#
# Author  : GCC Automation Team
# Version : 1.3
#
# CHANGELOG 1.2 -> 1.3
# --------------------
#   * Run selection, per-case export loop and summary now shared with
#     gcc_export_results_Rev2.py via gcc_common v2.0.
#   * FIX: completeness check uses the timestep cap the run actually used
#     (manifest 'dtgrd_max_s'; 0.1 s for FREQ runs from older manifests).
#     Previously FREQ cases were checked against the FRT 0.01 s cap and
#     falsely reported as incomplete.
# ==============================================================================

import sys
import os
import time as clock
from datetime import datetime

# ------------------------------------------------------------------------------
# Make gcc_common.py importable (it sits next to this script)
# ------------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcc_common as gcc


SCRIPT_VERSION = "1.3"

# ------------------------------------------------------------------------------
# PATH TO PROJECT CONFIG — same file used by the simulation scripts.
# May be a file OR a folder containing project_config(_update).yaml.
# ------------------------------------------------------------------------------
CONFIG_FILE = r"C:\Projects\GCC Script Development\Codemasters\Working Scripts\project_config_update.yaml"
# CONFIG_FILE = None    # -> use project_config.yaml next to this script

# ------------------------------------------------------------------------------
# WHICH STUDY FAMILY TO EXPORT:  "FRT"  or  "FREQ"
# ------------------------------------------------------------------------------
STUDY = "FRT"

# ------------------------------------------------------------------------------
# WHICH RUN TO EXPORT
#   None            -> single run: exported directly; several runs: a
#                      selection dialog opens (multi-select supported)
#   "FRT_2026-..."  -> that specific run folder, no dialog (automation path)
# ------------------------------------------------------------------------------
RUN_TAG = None

# Recorded variables and model elements needed per study family
_STUDY_SETTINGS = {
    "FRT":  {"record_vars": gcc.RECORD_VARS_FRT,
             "elements": ("poc_busbar", "poc_breaker", "inverter")},
    "FREQ": {"record_vars": gcc.RECORD_VARS_FREQ,
             "elements": ("poc_busbar", "poc_breaker")},
}


def export_run(app, sc_folder, elms, out_dir):
    """Export every solved case of ONE run folder; returns a summary dict."""
    run_tag     = sc_folder.loc_name
    record_vars = _STUDY_SETTINGS[STUDY]["record_vars"]
    app.PrintPlain(f"\n--- Exporting run '{run_tag}' ---")

    manifest, manifest_path = gcc.load_run_manifest(out_dir, run_tag)
    if manifest is None:
        raise RuntimeError(
            f"Run manifest not found:\n  {manifest_path}\n"
            "The manifest is written by the simulation scripts on every run "
            "and is the authority for what this exporter writes. If it is "
            "missing, re-run the simulation script (setup_only=true in "
            "run_sim_only mode is enough to regenerate it for an existing "
            "run folder).")
    cases = manifest.get("cases", [])
    app.PrintPlain(f"  Manifest : {manifest_path}")
    app.PrintPlain(f"  Cases    : {len(cases)} in manifest, "
                   f"{sum(1 for c in cases if c.get('status') == 'solved')} solved")
    if not cases:
        raise RuntimeError("Manifest contains no cases — nothing to export.")

    def export_case(study_case, csv_path):
        n_rows, last_t = gcc.export_case_to_csv(app, study_case, elms,
                                                record_vars, csv_path)
        return n_rows, last_t, {}

    export_dir = os.path.join(out_dir, run_tag)
    dtgrd_max  = manifest.get("dtgrd_max_s", gcc.DTGRD_MAX_DEFAULT[STUDY])
    entries, counts = gcc.export_run_cases(app, sc_folder, cases, export_dir,
                                           export_case, dtgrd_max)

    gcc.write_export_manifest(export_dir, {
        "run_tag":         run_tag,
        "study":           STUDY,
        "exported":        datetime.now().isoformat(timespec="seconds"),
        "exporter":        f"gcc_export_results.py v{SCRIPT_VERSION}",
        "source_manifest": manifest_path,
        "export_dir":      export_dir,
        "record_vars":     [{"element": e, "variable": v, "column": l}
                            for e, v, l in record_vars],
        "time_base_note":  ("NON-UNIFORM time base (iopt_adapt=1): "
                            "interpolate on time_s, never index by row."),
        "project":         manifest.get("project"),
        "plant":           manifest.get("plant"),
        "grid_code_file":  manifest.get("grid_code_file"),
        "grid_code_meta":  manifest.get("grid_code_meta"),
        "n_exported":      counts["ok"],
        "n_skipped":       counts["skip"],
        "n_errors":        counts["err"],
        "cases":           entries,
    })
    return {"run_tag": run_tag, "export_dir": export_dir, "n_total": len(cases),
            "n_ok": counts["ok"], "n_skip": counts["skip"], "n_err": counts["err"]}


def _run(app):
    if STUDY not in _STUDY_SETTINGS:
        raise RuntimeError(f"STUDY must be 'FRT' or 'FREQ', got '{STUDY}'")
    gcc.print_banner(app, f"GCC Results Export ({STUDY}) v{SCRIPT_VERSION} — starting")
    t0 = clock.perf_counter()

    config_path = gcc.resolve_config_path(CONFIG_FILE, __file__)
    app.PrintPlain(f"\nLoading project config : {config_path}")
    cfg     = gcc.load_yaml(config_path)
    out_dir = cfg["results"]["output_dir"]

    elms = gcc.find_elements(app, cfg["elements"], _STUDY_SETTINGS[STUDY]["elements"])

    run_folders = gcc.select_run_folders(app, STUDY, RUN_TAG)
    app.PrintPlain(f"Selected : {[f.loc_name for f in run_folders]}")

    gcc.export_runs(app, STUDY, run_folders,
                    lambda sc_folder: export_run(app, sc_folder, elms, out_dir))
    app.PrintPlain(f"\n  Runtime : {round(clock.perf_counter() - t0, 2)}s")
    app.PrintPlain(gcc.RULE)


def main():
    gcc.run_script("GCC Results Export", _run)


if __name__ == "__main__":
    main()
