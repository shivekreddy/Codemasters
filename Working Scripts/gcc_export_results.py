# ==============================================================================
# GCC Results Export Script (FRT or Frequency)
# ==============================================================================
# Exports the raw simulation results of one run to ONE CSV FILE PER STUDY
# CASE. Run it after the FRT or frequency script has run the simulations.
# It creates and runs nothing; it only reads result files.
#
# What it does:
#   1. Picks the run folder: RUN_TAG below, or the latest FRT_* / FREQ_* folder
#   2. Takes the case list from the run manifest written by the simulation
#      script ({run_tag}_manifest.json in results.output_dir). Failed cases are
#      skipped. Without a manifest, every study case in the run folder is used.
#   3. Writes one CSV per case, named after the study case:
#        "FRT 3PH 0.05 Q0" -> FRT_3PH_0.05_Q0.csv
#        "LFSMO Step"      -> LFSMO_Step.csv
#   4. Writes export_manifest.json next to the CSVs (case -> file + metadata),
#      which the evaluation script reads.
#
# Output layout:
#   {results.output_dir}\
#     FRT_2026-10-16_13-50-06\       <- one folder per exported run
#       FRT_3PH_0.05_Q0.csv
#       ...
#       export_manifest.json
#
# CSV format: time_s + one column per recorded variable, with readable
# headers (e.g. "POC Active Power [MW]"). The variables and headers are
# RECORD_VARS_FRT / RECORD_VARS_FREQ in gcc_common.py, the same lists the
# simulation scripts record.
#   NOTE: the time base is NON-UNIFORM (adaptive step size): interpolate on
#   time_s, never index by row.
#
# Usage:
#   1. Set STUDY below to "FRT" or "FREQ"
#   2. Point a ComPython object at this script and run it from PowerFactory
#
# Dependencies: PyYAML, gcc_common.py in the same folder as this script.
# ==============================================================================

import sys
import os
import json
import time as clock
from datetime import datetime

# ------------------------------------------------------------------------------
# Make gcc_common.py importable (it sits next to this script)
# ------------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcc_common as gcc

import powerfactory


# ------------------------------------------------------------------------------
# WHICH STUDY TO EXPORT:  "FRT"  or  "FREQ"
# ------------------------------------------------------------------------------
STUDY = "FRT"

# ------------------------------------------------------------------------------
# WHICH RUN TO EXPORT
#   None                       -> the latest FRT_* / FREQ_* run folder
#   "FRT_2026-10-16_13-50-06"  -> that specific run folder
# ------------------------------------------------------------------------------
RUN_TAG = None

# ------------------------------------------------------------------------------
# PATH TO PROJECT CONFIG (same file as the simulation scripts)
#   None -> project_config.yaml / project_config_update.yaml next to this script
# ------------------------------------------------------------------------------
CONFIG_FILE = None


# Per-study settings. dtgrd_max_s must match DTGRD_MAX_S in the simulation
# script; it sets the tolerance of the "did the case reach t_stop" check.
_STUDY_SETTINGS = {
    "FRT":  {"record_vars": gcc.RECORD_VARS_FRT,  "dtgrd_max_s": 0.01},
    "FREQ": {"record_vars": gcc.RECORD_VARS_FREQ, "dtgrd_max_s": 0.1},
}


# ==============================================================================
# 1.  CONFIG AND ELEMENTS
# ==============================================================================

def _resolve_config_path():
    """CONFIG_FILE (file OR folder), else the config next to this script."""
    path = CONFIG_FILE or os.path.dirname(os.path.abspath(__file__))
    if os.path.isdir(path):     # folder given -> look for a config inside it
        for name in ("project_config.yaml", "project_config_update.yaml"):
            candidate = os.path.join(path, name)
            if os.path.isfile(candidate):
                return candidate
    return path


def find_recorded_elements(app, elem_cfg, record_vars):
    """
    Look up only the elements that record_vars actually uses.
    Returns {element_key: PF object}.
    """
    needed = []
    for elm_key, _var, _label in record_vars:
        if elm_key not in needed:
            needed.append(elm_key)

    elm_objects = {}
    for elm_key in needed:
        if elm_key not in elem_cfg:
            raise RuntimeError(f"Missing 'elements.{elm_key}' in project config.")
        name = elem_cfg[elm_key]
        if elm_key == "inverter":
            # Inverter may be ElmGenstat (PV/storage) or ElmSym (wind)
            obj = (gcc.find_element(app, "ElmGenstat", name, required=False)
                   or gcc.find_element(app, "ElmSym", name))
        elif elm_key == "poc_breaker":
            obj = gcc.find_element(app, "ElmCoup", name)
        else:
            obj = gcc.find_element(app, "ElmTerm", name)
        elm_objects[elm_key] = obj
        app.PrintPlain(f"  [ok] {elm_key:12s}: {obj.loc_name}")
    return elm_objects


# ==============================================================================
# 2.  RUN FOLDER AND CASE LIST
# ==============================================================================

def select_run_folder(sc_folder_root):
    """RUN_TAG if set, else the latest {STUDY}_* run folder."""
    if RUN_TAG:
        matches = sc_folder_root.GetContents(f"{RUN_TAG}.IntFolder")
        if not matches:
            raise RuntimeError(f"Run folder '{RUN_TAG}' not found in Study Cases.")
        return matches[0]

    folder = gcc.find_latest_run_folder(sc_folder_root, STUDY)
    if folder is None:
        raise RuntimeError(
            f"No {STUDY}_* run folder found in Study Cases. "
            f"Run the {STUDY} simulation script first."
        )
    return folder


def load_case_list(app, sc_folder, out_dir):
    """
    Case list for one run.
    Preferred: the run manifest (case metadata + solved/failed status).
    Fallback:  every study case in the run folder, without metadata.
    Returns (cases, manifest, manifest_path); manifest is None in fallback.
    """
    run_tag = sc_folder.loc_name
    manifest_path = os.path.join(out_dir, f"{run_tag}_manifest.json")

    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        if manifest.get("cases"):
            return manifest["cases"], manifest, manifest_path
        app.PrintWarn("  Run manifest lists no cases; using the run folder instead.")
    else:
        app.PrintWarn(
            f"  Run manifest not found ({manifest_path}); exporting every study "
            f"case in the run folder. Case metadata will be missing."
        )

    cases = [{"name": c.loc_name, "status": "unknown"}
             for c in sc_folder.GetContents("*.IntCase")]
    if not cases:
        raise RuntimeError(f"Run folder '{run_tag}' contains no study cases.")
    cases.sort(key=lambda c: c["name"])
    return cases, None, None


# ==============================================================================
# 3.  MAIN
# ==============================================================================

def _run(app):

    if STUDY not in _STUDY_SETTINGS:
        raise RuntimeError(f"STUDY must be 'FRT' or 'FREQ', got '{STUDY}'.")
    record_vars = _STUDY_SETTINGS[STUDY]["record_vars"]
    tolerance   = 2.0 * _STUDY_SETTINGS[STUDY]["dtgrd_max_s"]

    app.PrintPlain("=" * 68)
    app.PrintPlain(f"  GCC Results Export ({STUDY}) starting")
    app.PrintPlain("=" * 68)
    t0 = clock.perf_counter()

    # --- Config and output folder ---------------------------------------------
    config_path = _resolve_config_path()
    app.PrintPlain(f"\nLoading project config : {config_path}")
    cfg     = gcc.load_yaml(config_path)
    out_dir = cfg["results"]["output_dir"]

    # --- Elements used by the recorded variables ------------------------------
    app.PrintPlain("\nElements:")
    elm_objects = find_recorded_elements(app, cfg["elements"], record_vars)

    # --- Run folder and cases -------------------------------------------------
    sc_folder = select_run_folder(gcc.get_folder(app, "study"))
    run_tag   = sc_folder.loc_name
    app.PrintPlain(f"\nRun folder : '{run_tag}'")

    cases, manifest, manifest_path = load_case_list(app, sc_folder, out_dir)

    export_dir = os.path.join(out_dir, run_tag)
    os.makedirs(export_dir, exist_ok=True)
    app.PrintPlain(f"Export to  : {export_dir}")
    app.PrintPlain(f"Cases      : {len(cases)}\n")

    # --- Export each case -----------------------------------------------------
    entries = []
    n_ok, n_skip, n_err = 0, 0, 0

    for case in cases:
        name  = case["name"]
        entry = dict(case)          # carry all manifest metadata through
        entry["csv_file"] = None

        if case.get("status") == "failed":
            app.PrintWarn(f"  [{name}] skipped: failed in simulation "
                          f"({case.get('reason')})")
            entry["export_status"] = "skipped_failed"
            entries.append(entry)
            n_skip += 1
            continue

        matches = sc_folder.GetContents(f"{name}.IntCase")
        if not matches:
            app.PrintWarn(f"  [{name}] not found in '{run_tag}', skipped.")
            entry["export_status"] = "case_not_found"
            entries.append(entry)
            n_err += 1
            continue

        fname    = gcc.case_csv_filename(name)
        csv_path = os.path.join(export_dir, fname)

        try:
            n_rows, last_t = gcc.export_case_to_csv(
                app, matches[0], elm_objects, record_vars, csv_path
            )
        except Exception as e:
            app.PrintWarn(f"  [{name}] export FAILED: {e}")
            entry["export_status"] = f"export_error: {e}"
            entries.append(entry)
            n_err += 1
            continue

        if n_rows == 0 or last_t is None:
            app.PrintWarn(f"  [{name}] no result data (not simulated yet?), "
                          f"no CSV written.")
            if os.path.isfile(csv_path):
                os.remove(csv_path)
            entry["export_status"] = "no_result_data"
            entries.append(entry)
            n_err += 1
            continue

        # Completeness check: only possible when the manifest gives t_stop
        t_stop = case.get("t_stop")
        if t_stop is not None:
            entry["complete"] = last_t >= float(t_stop) - tolerance
            if not entry["complete"]:
                app.PrintWarn(f"  [{name}] result ends at t={last_t:.3f}s of "
                              f"{float(t_stop):.1f}s; exported, but the case "
                              f"looks incomplete.")

        entry["csv_file"]      = fname
        entry["export_status"] = "ok"
        entry["n_rows"]        = n_rows
        entry["t_end_s"]       = round(last_t, 6)
        entries.append(entry)
        n_ok += 1
        app.PrintPlain(f"  [{name}] -> {fname}  ({n_rows} rows, "
                       f"t_end={last_t:.3f}s)")

    # --- Export manifest: the evaluation script's entry point -----------------
    export_manifest = {
        "run_tag":         run_tag,
        "study":           STUDY,
        "exported":        datetime.now().isoformat(timespec="seconds"),
        "source_manifest": manifest_path,
        "columns":         [{"element": e, "variable": v, "column": lbl}
                            for e, v, lbl in record_vars],
        "time_base_note":  "NON-UNIFORM time base: interpolate on time_s.",
        "project":         (manifest or {}).get("project"),
        "plant":           (manifest or {}).get("plant"),
        "grid_code_file":  (manifest or {}).get("grid_code_file"),
        "grid_code_meta":  (manifest or {}).get("grid_code_meta"),
        "n_exported":      n_ok,
        "n_skipped":       n_skip,
        "n_errors":        n_err,
        "cases":           entries,
    }
    export_manifest_path = os.path.join(export_dir, "export_manifest.json")
    with open(export_manifest_path, "w", encoding="utf-8") as f:
        json.dump(export_manifest, f, indent=2, default=str)

    # --- Summary --------------------------------------------------------------
    app.PrintPlain("")
    app.PrintPlain("=" * 68)
    app.PrintPlain(f"  {STUDY} RESULTS EXPORT SUMMARY")
    app.PrintPlain("=" * 68)
    app.PrintPlain(f"  Run      : {run_tag}")
    app.PrintPlain(f"  Exported : {n_ok} / {len(cases)} cases"
                   + (f", {n_skip} skipped (failed)" if n_skip else "")
                   + (f", {n_err} errors" if n_err else ""))
    app.PrintPlain(f"  Folder   : {export_dir}")
    app.PrintPlain(f"  Manifest : {export_manifest_path}")
    app.PrintPlain(f"  Runtime  : {round(clock.perf_counter() - t0, 2)}s")
    app.PrintPlain("=" * 68)


def main():
    app = powerfactory.GetApplication()
    with gcc.pf_session(app):
        try:
            _run(app)
        except Exception as exc:
            app.PrintError(f"GCC Results Export aborted: {exc}")
            raise


# ==============================================================================
# Entry point
# ==============================================================================
main()
