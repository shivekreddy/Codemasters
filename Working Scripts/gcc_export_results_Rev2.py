# ==============================================================================
# GCC Results Export Script — FRT and Frequency (standalone, self-contained)
# ==============================================================================
# Exports simulation results from completed runs to ONE CSV FILE PER STUDY
# CASE. Run this after the simulations have finished (any execution mode).
# It creates and runs nothing — it only reads result files.
#
# FULLY INDEPENDENT of the project config:
#   * does NOT read project_config.yaml (no PyYAML needed),
#   * does NOT need any element names — it enumerates the columns of each
#     case's result file directly and exports EVERYTHING that was recorded,
#   * writes to the OUTPUT_DIR defined below.
#   The only shared dependency is gcc_common.py.
#
# RUN SELECTION (in priority order):
#   1. RUN_TAG set below          -> that folder, no dialog (automation path)
#   2. exactly one {STUDY}_* run  -> exported, no dialog
#   3. several runs               -> PowerFactory selection dialog opens.
#      MULTI-SELECT is supported: Ctrl-click several runs and each one is
#      exported into its own subfolder. Cancel aborts cleanly.
#
# RUN MANIFEST (optional but recommended):
#   If {run_tag}_manifest.json exists in OUTPUT_DIR (it does when the
#   simulation scripts' results.output_dir is the same folder), its per-case
#   metadata (uret, t_fault, t_clear, t_stop, setpoints, status) is carried
#   into the export manifest and failed cases are skipped. Without it, every
#   study case in the run folder is exported and case metadata is marked
#   unavailable — the CSVs are still complete.
#
# Output layout:
#
#   {OUTPUT_DIR}\
#     FRT_2026-07-09_14-30-54\          <- one folder per exported run
#       FRT_3PH_0.05_Q0.csv             <- one CSV per case
#       ...
#       export_manifest.json            <- case -> file + column mapping
#
# CSV format per file:
#   time_s, <one column per variable recorded in the result file>
#   Column headers are self-describing:  "{element name} | {variable}"
#     e.g.  "POC | m:u1",  "POC Breaker | m:Psum:bus1"
#   The export manifest lists every column with element name, class, and
#   variable — the evaluation tool reads the mapping from there, never from
#   header-string parsing.
#   NOTE: the time base is NON-UNIFORM (iopt_adapt=1) — the evaluation tool
#   must interpolate on time_s, never index by row.
#
# Usage:
#   1. Set STUDY ("FRT" or "FREQ") and OUTPUT_DIR below
#   2. Point a ComPython object at this script and run from PowerFactory
#
# Dependencies: gcc_common.py v2.0+ in the same folder as this script.
#
# Author  : GCC Automation Team
# Version : 2.1
#
# CHANGELOG 2.0 -> 2.1
# --------------------
#   * Run selection, per-case export loop and summary now shared with
#     gcc_export_results.py via gcc_common v2.0.
#   * FIX: completeness check uses the timestep cap the run actually used
#     (manifest 'dtgrd_max_s'; 0.1 s for FREQ runs from older manifests).
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


SCRIPT_VERSION = "2.1"

# ------------------------------------------------------------------------------
# OUTPUT DIRECTORY — where the per-run export folders are written.
# Also where {run_tag}_manifest.json files are looked for (optional).
# ------------------------------------------------------------------------------
OUTPUT_DIR = r"C:\Users\SHIRED\Downloads"

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


# ==============================================================================
# 1.  CASE LIST — manifest if available, folder contents otherwise
# ==============================================================================

def load_case_list(app, sc_folder, run_tag):
    """
    Cases to export for one run: from {run_tag}_manifest.json in OUTPUT_DIR
    (full metadata + solved/failed status), else every IntCase in the run
    folder with metadata unavailable.

    Returns (cases, manifest, manifest_path) — manifest and manifest_path
    are None in fallback mode.
    """
    manifest, manifest_path = gcc.load_run_manifest(OUTPUT_DIR, run_tag)
    if manifest is not None:
        if manifest.get("cases"):
            return manifest["cases"], manifest, manifest_path
        app.PrintWarn("  Run manifest contains no cases — falling back to "
                      "the run folder contents.")
    else:
        app.PrintWarn(
            f"  Run manifest not found in OUTPUT_DIR ({run_tag}_manifest.json) "
            f"— exporting every study case in the run folder. Case metadata "
            f"(uret, t_fault, t_clear, setpoints, solved/failed status) will "
            f"be UNAVAILABLE in the export manifest.")

    cases = sorted(({"name": c.loc_name, "status": "unknown"}
                    for c in sc_folder.GetContents("*.IntCase")),
                   key=lambda c: c["name"])
    if not cases:
        raise RuntimeError("Run folder contains no study cases.")
    return cases, None, None


# ==============================================================================
# 2.  PER-CASE EXPORT — every recorded column, self-describing headers
# ==============================================================================

def enumerate_result_columns(elmRes):
    """
    All columns of a loaded ElmRes as dicts
      { "column": i, "element": loc_name, "class": class_name,
        "variable": var, "header": "element | variable" }
    Duplicate headers (two same-named elements recording the same variable)
    are disambiguated with the column index.
    """
    cols, seen = [], set()
    for i in range(elmRes.GetNumberOfColumns()):
        try:
            obj = elmRes.GetObject(i)
            var = elmRes.GetVariable(i)
        except Exception:
            obj, var = None, None
        if not var:
            continue
        name = getattr(obj, "loc_name", "?") if obj is not None else "?"
        cls  = obj.GetClassName() if obj is not None else "?"
        header = f"{name} | {var}"
        if header in seen:
            header = f"{name} | {var} [{i}]"
        seen.add(header)
        cols.append({"column": i, "element": name, "class": cls,
                     "variable": var, "header": header})
    return cols


def export_case_all_columns(app, study_case, csv_path):
    """
    One study case -> one CSV with every recorded column.
    Returns (n_rows, last_t, {'n_columns': N, '_columns': [...]}).
    """
    with gcc.loaded_results(app, study_case) as elmRes:
        n_rows = elmRes.GetNumberOfRows()
        if n_rows == 0:
            return 0, None, {}
        columns = enumerate_result_columns(elmRes)
        if not columns:
            return 0, None, {}
        last_t = gcc.write_result_csv(elmRes, csv_path,
                                      [c["header"] for c in columns],
                                      [c["column"] for c in columns])
        return n_rows, last_t, {"n_columns": len(columns), "_columns": columns}


# ==============================================================================
# 3.  PER-RUN EXPORT
# ==============================================================================

def export_run(app, sc_folder):
    """Export every exportable case of ONE run folder; returns a summary dict."""
    run_tag = sc_folder.loc_name
    app.PrintPlain(f"\n--- Exporting run '{run_tag}' ---")

    cases, manifest, manifest_path = load_case_list(app, sc_folder, run_tag)
    n_todo = sum(1 for c in cases if c.get("status") != "failed")
    app.PrintPlain(f"  Cases    : {len(cases)} listed, {n_todo} to export"
                   + ("" if manifest else "  [no run manifest — metadata unavailable]"))

    export_dir = os.path.join(OUTPUT_DIR, run_tag)
    dtgrd_max  = (manifest or {}).get("dtgrd_max_s", gcc.DTGRD_MAX_DEFAULT[STUDY])
    entries, counts = gcc.export_run_cases(
        app, sc_folder, cases, export_dir,
        lambda study_case, csv_path: export_case_all_columns(app, study_case, csv_path),
        dtgrd_max)

    # The column mapping of the first exported case is the run's mapping.
    run_columns = next((e["_columns"] for e in entries if "_columns" in e), [])
    for e in entries:
        e.pop("_columns", None)

    manifest = manifest or {}
    gcc.write_export_manifest(export_dir, {
        "run_tag":         run_tag,
        "study":           STUDY,
        "exported":        datetime.now().isoformat(timespec="seconds"),
        "exporter":        f"gcc_export_results_Rev2.py v{SCRIPT_VERSION}",
        "source_manifest": manifest_path,     # None in fallback mode
        "export_dir":      export_dir,
        "columns":         run_columns,
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
    return {"run_tag": run_tag, "export_dir": export_dir,
            "had_manifest": manifest_path is not None, "n_total": len(cases),
            "n_ok": counts["ok"], "n_skip": counts["skip"], "n_err": counts["err"]}


# ==============================================================================
# 4.  MAIN
# ==============================================================================

def _run(app):
    if STUDY not in ("FRT", "FREQ"):
        raise RuntimeError(f"STUDY must be 'FRT' or 'FREQ', got '{STUDY}'")
    gcc.print_banner(app, f"GCC Results Export ({STUDY}) v{SCRIPT_VERSION} — starting")
    t0 = clock.perf_counter()

    gcc.check_output_dir(OUTPUT_DIR)
    app.PrintPlain(f"\nOutput   : {OUTPUT_DIR}")

    run_folders = gcc.select_run_folders(app, STUDY, RUN_TAG)
    app.PrintPlain(f"Selected : {[f.loc_name for f in run_folders]}")

    gcc.export_runs(app, STUDY, run_folders,
                    lambda sc_folder: export_run(app, sc_folder))
    app.PrintPlain(f"\n  Runtime : {round(clock.perf_counter() - t0, 2)}s")
    app.PrintPlain(gcc.RULE)


def main():
    gcc.run_script("GCC Results Export", _run)


if __name__ == "__main__":
    main()
