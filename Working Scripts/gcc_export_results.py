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
#      exported into its own subfolder — built for parameter-comparison
#      workflows. Cancel aborts cleanly.
#
# The run's JSON manifest is the authority for what gets exported:
#   * the case list and their solved/failed status come from the manifest,
#   * CSV file names are derived from the manifest case names
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
#     FRT_2026-07-12_13-44-25\          <- second selected run, same layout
#       ...
#
# CSV format per file:
#   time_s, <one column per record_vars entry>
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
# Dependencies: PyYAML, gcc_common.py v1.1+ in the same folder as this script.
#
# Author  : GCC Automation Team
# Version : 1.2
#
# CHANGELOG 1.1 -> 1.2
# --------------------
#   * NEW run selection: when several {STUDY}_* run folders exist and RUN_TAG
#     is not set, a PowerFactory selection dialog opens
#     (Application.ShowModalSelectBrowser). Multiple runs can be selected and
#     are exported in one go, each into its own subfolder — parameter
#     comparison across runs without editing the script.
#   * Per-run error isolation: a run that cannot be exported (e.g. its
#     manifest was deleted from the output folder) is reported and skipped;
#     the remaining selected runs still export.
#   * Combined end-of-run summary across all exported runs.
#
# CHANGELOG 1.0 -> 1.1
# --------------------
#   * ONE exporter for BOTH study families (STUDY switch); recorded variables,
#     naming convention, and per-case export logic moved to gcc_common;
#     CONFIG_FILE may point at a folder.
# ==============================================================================

import sys
import os
import json
import time as clock

# ------------------------------------------------------------------------------
# Make gcc_common.py importable (it sits next to this script)
# ------------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcc_common as gcc

import powerfactory


# ------------------------------------------------------------------------------
# PATH TO PROJECT CONFIG — same file used by the simulation scripts.
# May be a file OR a folder containing project_config(_update).yaml.
# ------------------------------------------------------------------------------
CONFIG_FILE = r"C:\Projects\GCC Script Development\Codemasters\Working Scripts\project_config_update.yaml"
# CONFIG_FILE = None    # -> use project_config.yaml next to this script

# ------------------------------------------------------------------------------
# WHICH STUDY FAMILY TO EXPORT:  "FRT"  or  "FREQ"
#   Selects the run-folder prefix (FRT_* / FREQ_*), the recorded-variable
#   list, and the model elements required.
# ------------------------------------------------------------------------------
STUDY = "FRT"

# ------------------------------------------------------------------------------
# WHICH RUN TO EXPORT
#   None            -> single run: exported directly; several runs: a
#                      selection dialog opens (multi-select supported)
#   "FRT_2026-..."  -> that specific run folder, no dialog (automation path)
# ------------------------------------------------------------------------------
RUN_TAG = None

_STUDY_SETTINGS = {
    "FRT":  {"prefix": "FRT",  "record_vars": gcc.RECORD_VARS_FRT,
             "needs_inverter": True},
    "FREQ": {"prefix": "FREQ", "record_vars": gcc.RECORD_VARS_FREQ,
             "needs_inverter": False},
}
if STUDY not in _STUDY_SETTINGS:
    raise RuntimeError(f"STUDY must be 'FRT' or 'FREQ', got '{STUDY}'")
RUN_PREFIX  = _STUDY_SETTINGS[STUDY]["prefix"]
RECORD_VARS = _STUDY_SETTINGS[STUDY]["record_vars"]

# Tolerance basis for the completeness check: last result time must reach
# t_stop - 2 x dtgrd_max (time base is non-uniform under iopt_adapt=1).
DTGRD_MAX_S = 0.01


# ==============================================================================
# 1.  RUN SELECTION
# ==============================================================================

def select_run_folders(app, sc_folder_root):
    """
    Decide which run folder(s) to export, in priority order:
      1. RUN_TAG set              -> that folder only, no dialog
      2. one {STUDY}_* folder     -> exported directly, no dialog
      3. several                  -> PF selection dialog, MULTI-SELECT
                                     supported; newest listed first;
                                     Cancel aborts cleanly

    Returns a list of IntFolder objects (never empty — raises instead).
    """
    if RUN_TAG:
        matches = sc_folder_root.GetContents(f"{RUN_TAG}.IntFolder")
        if not matches:
            raise RuntimeError(
                f"Run folder '{RUN_TAG}' not found in Study Cases."
            )
        return [matches[0]]

    candidates = []
    for f in sc_folder_root.GetContents(f"{RUN_PREFIX}_*.IntFolder"):
        ts = gcc._parse_run_tag(f.loc_name, RUN_PREFIX)
        if ts is not None:
            candidates.append((ts, f))

    if not candidates:
        raise RuntimeError(
            f"No {RUN_PREFIX}_* run folder found in Study Cases. "
            f"Run the {STUDY} simulation script first."
        )

    candidates.sort(key=lambda p: p[0], reverse=True)   # newest first

    if len(candidates) == 1:
        return [candidates[0][1]]

    app.PrintPlain(
        f"\n{len(candidates)} {RUN_PREFIX} run folders found — select the "
        f"run(s) to export in the dialog (Ctrl-click for several; newest "
        f"listed first)."
    )
    selected = app.ShowModalSelectBrowser(
        [f for _, f in candidates],
        f"Select the {STUDY} run(s) to export",
    )
    if not selected:
        raise RuntimeError("Export cancelled — no run folder selected.")
    return list(selected)


# ==============================================================================
# 2.  PER-RUN EXPORT
# ==============================================================================

def load_run_manifest(out_dir, run_tag):
    """Load {run_tag}_manifest.json from the output folder. Required."""
    path = os.path.join(out_dir, f"{run_tag}_manifest.json")
    if not os.path.isfile(path):
        raise RuntimeError(
            f"Run manifest not found:\n  {path}\n"
            "The manifest is written by the simulation scripts on every run "
            "and is the authority for what this exporter writes. If it is "
            "missing, re-run the simulation script (setup_only=true in "
            "run_sim_only mode is enough to regenerate it for an existing "
            "run folder)."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f), path


def export_run(app, sc_folder, elm_objects, out_dir):
    """
    Export every solved case of ONE run folder to per-case CSVs plus an
    export_manifest.json.

    Returns a summary dict:
      { run_tag, export_dir, manifest_path, n_total, n_ok, n_skip, n_err }
    Raises RuntimeError if the run cannot be exported at all (e.g. manifest
    missing) — the caller isolates that per run.
    """
    run_tag = sc_folder.loc_name
    app.PrintPlain(f"\n--- Exporting run '{run_tag}' ---")

    manifest, manifest_path = load_run_manifest(out_dir, run_tag)
    cases = manifest.get("cases", [])
    app.PrintPlain(f"  Manifest : {manifest_path}")
    app.PrintPlain(f"  Cases    : {len(cases)} in manifest, "
                   f"{sum(1 for c in cases if c.get('status') == 'solved')} solved")

    if not cases:
        raise RuntimeError("Manifest contains no cases — nothing to export.")

    export_dir = os.path.join(out_dir, run_tag)
    os.makedirs(export_dir, exist_ok=True)

    export_entries = []
    n_ok, n_skip, n_err = 0, 0, 0

    for case in cases:
        name   = case["name"]
        status = case.get("status", "solved")
        entry  = dict(case)              # carry ALL manifest metadata through

        if status != "solved":
            app.PrintWarn(f"  [{name}] skipped — status '{status}' "
                          f"({case.get('reason')})")
            entry["csv_file"] = None
            entry["export_status"] = "skipped_not_solved"
            export_entries.append(entry)
            n_skip += 1
            continue

        matches = sc_folder.GetContents(f"{name}.IntCase")
        if not matches:
            app.PrintWarn(f"  [{name}] NOT FOUND in '{run_tag}' — skipped.")
            entry["csv_file"] = None
            entry["export_status"] = "case_not_found"
            export_entries.append(entry)
            n_err += 1
            continue

        fname    = gcc.case_csv_filename(name)
        csv_path = os.path.join(export_dir, fname)

        try:
            n_rows, last_t = gcc.export_case_to_csv(
                app, matches[0], elm_objects, RECORD_VARS, csv_path
            )
        except Exception as e:
            app.PrintWarn(f"  [{name}] export FAILED: {e}")
            entry["csv_file"] = None
            entry["export_status"] = f"export_error: {e}"
            export_entries.append(entry)
            n_err += 1
            continue

        if n_rows == 0 or last_t is None:
            app.PrintWarn(f"  [{name}] no result data — empty file removed.")
            try:
                os.remove(csv_path)
            except OSError:
                pass
            entry["csv_file"] = None
            entry["export_status"] = "no_result_data"
            export_entries.append(entry)
            n_err += 1
            continue

        # Completeness note (does not block the export)
        t_stop   = float(case.get("t_stop", 0.0))
        complete = last_t >= (t_stop - 2.0 * DTGRD_MAX_S)
        if not complete:
            app.PrintWarn(
                f"  [{name}] result ends at t={last_t:.3f}s of "
                f"{t_stop:.1f}s — exported, but case looks incomplete."
            )

        entry["csv_file"]      = fname          # relative to export_dir
        entry["export_status"] = "ok"
        entry["n_rows"]        = n_rows
        entry["t_end_s"]       = round(last_t, 6)
        entry["complete"]      = complete
        export_entries.append(entry)
        n_ok += 1
        app.PrintPlain(f"  [{name}] -> {fname}  ({n_rows} rows, "
                       f"t_end={last_t:.3f}s)")

    # --------------------------------------------------------------------------
    # Export manifest — the evaluation tool's entry point for this run.
    # --------------------------------------------------------------------------
    export_manifest = {
        "run_tag":          run_tag,
        "study":            STUDY,
        "exported":         gcc.datetime.now().isoformat(timespec="seconds"),
        "source_manifest":  manifest_path,
        "export_dir":       export_dir,
        "record_vars":      [{"element": e, "variable": v, "column": l}
                             for e, v, l in RECORD_VARS],
        "time_base_note":   ("NON-UNIFORM time base (iopt_adapt=1): "
                             "interpolate on time_s, never index by row."),
        "project":          manifest.get("project"),
        "plant":            manifest.get("plant"),
        "grid_code_file":   manifest.get("grid_code_file"),
        "grid_code_meta":   manifest.get("grid_code_meta"),
        "n_exported":       n_ok,
        "n_skipped":        n_skip,
        "n_errors":         n_err,
        "cases":            export_entries,
    }
    export_manifest_path = os.path.join(export_dir, "export_manifest.json")
    with open(export_manifest_path, "w", encoding="utf-8") as f:
        json.dump(export_manifest, f, indent=2, default=str)

    return {
        "run_tag":       run_tag,
        "export_dir":    export_dir,
        "manifest_path": export_manifest_path,
        "n_total":       len(cases),
        "n_ok":          n_ok,
        "n_skip":        n_skip,
        "n_err":         n_err,
    }


# ==============================================================================
# 3.  MAIN
# ==============================================================================

def _resolve_config_path():
    """CONFIG_FILE (file OR folder), else project_config.yaml next to script."""
    path = CONFIG_FILE or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "project_config.yaml")
    if os.path.isdir(path):     # folder given -> look for a config inside it
        for name in ("project_config.yaml", "project_config_update.yaml"):
            candidate = os.path.join(path, name)
            if os.path.isfile(candidate):
                return candidate
    return path


def _run(app):

    app.PrintPlain("=" * 68)
    app.PrintPlain(f"  GCC Results Export ({STUDY}) v1.2 — starting")
    app.PrintPlain("=" * 68)
    t0 = clock.perf_counter()

    # --------------------------------------------------------------------------
    # 3.1  Config
    # --------------------------------------------------------------------------
    config_path = _resolve_config_path()
    app.PrintPlain(f"\nLoading project config : {config_path}")
    cfg      = gcc.load_yaml(config_path)
    elem_cfg = cfg["elements"]
    out_dir  = cfg["results"]["output_dir"]

    # --------------------------------------------------------------------------
    # 3.2  Resolve the elements referenced by RECORD_VARS
    # --------------------------------------------------------------------------
    POC_BB  = gcc.find_element(app, "ElmTerm", elem_cfg["poc_busbar"])
    POC_BRK = gcc.find_element(app, "ElmCoup", elem_cfg["poc_breaker"])
    elm_objects = {"poc_busbar": POC_BB, "poc_breaker": POC_BRK}
    elm_line = (f"Elements : POC='{POC_BB.loc_name}'  "
                f"Breaker='{POC_BRK.loc_name}'")

    if _STUDY_SETTINGS[STUDY]["needs_inverter"]:
        Inverter = (gcc.find_element(app, "ElmGenstat", elem_cfg["inverter"],
                                     required=False)
                    or gcc.find_element(app, "ElmSym", elem_cfg["inverter"]))
        elm_objects["inverter"] = Inverter
        elm_line += f"  Inverter='{Inverter.loc_name}'"
    app.PrintPlain(elm_line)

    # --------------------------------------------------------------------------
    # 3.3  Select the run(s) to export
    # --------------------------------------------------------------------------
    sc_folder_root = gcc.get_folder(app, "study")
    run_folders    = select_run_folders(app, sc_folder_root)
    app.PrintPlain(f"Selected : {[f.loc_name for f in run_folders]}")

    # --------------------------------------------------------------------------
    # 3.4  Export each selected run — one failure never stops the others
    # --------------------------------------------------------------------------
    summaries  = []
    run_errors = {}   # {run_tag: reason}

    for sc_folder in run_folders:
        try:
            summaries.append(export_run(app, sc_folder, elm_objects, out_dir))
        except Exception as e:
            run_errors[sc_folder.loc_name] = str(e)
            app.PrintWarn(f"  Run '{sc_folder.loc_name}' NOT exported: {e}")

    # --------------------------------------------------------------------------
    # 3.5  Combined summary
    # --------------------------------------------------------------------------
    elapsed = round(clock.perf_counter() - t0, 2)
    app.PrintPlain("")
    app.PrintPlain("=" * 68)
    app.PrintPlain(f"  {STUDY} RESULTS EXPORT SUMMARY")
    app.PrintPlain("=" * 68)
    app.PrintPlain(f"  Runs selected : {len(run_folders)}   "
                   f"exported: {len(summaries)}   failed: {len(run_errors)}")

    for s in summaries:
        line = (f"  [ok] {s['run_tag']}  —  {s['n_ok']}/{s['n_total']} cases")
        if s["n_skip"]:
            line += f", {s['n_skip']} skipped"
        if s["n_err"]:
            line += f", {s['n_err']} errors"
        app.PrintPlain(line)
        app.PrintPlain(f"       -> {s['export_dir']}")

    for tag, reason in run_errors.items():
        app.PrintPlain(f"  [x]  {tag}  —  {reason.splitlines()[0]}")

    app.PrintPlain(f"\n  Runtime : {elapsed}s")
    app.PrintPlain("=" * 68)


def main():
    app = powerfactory.GetApplication()
    with gcc.pf_session(app):
        try:
            _run(app)
        except Exception as exc:
            app.PrintError(f"GCC Results Export aborted: {exc}")
            raise


main()
