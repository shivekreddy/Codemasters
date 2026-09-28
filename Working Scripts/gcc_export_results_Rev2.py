# ==============================================================================
# GCC Results Export Script — FRT and Frequency (standalone, self-contained)
# ==============================================================================
# Exports simulation results from completed runs to ONE CSV FILE PER STUDY
# CASE. Run this after the simulations have finished (any execution mode).
# It creates and runs nothing — it only reads result files.
#
# FULLY INDEPENDENT of the other scripts:
#   * does NOT read project_config.yaml (no PyYAML needed),
#   * does NOT need any element names — it enumerates the columns of each
#     case's result file directly and exports EVERYTHING that was recorded,
#   * writes to the OUTPUT_DIR defined below.
#   The only shared dependency is gcc_common.py (folder parsing, UI wrapper).
#
# RUN SELECTION (in priority order):
#   1. RUN_TAG set below          -> that folder, no dialog (automation path)
#   2. exactly one {STUDY}_* run  -> exported, no dialog
#   3. several runs               -> PowerFactory selection dialog opens.
#      MULTI-SELECT is supported: Ctrl-click several runs and each one is
#      exported into its own subfolder — built for parameter-comparison
#      workflows. Cancel aborts cleanly.
#
# RUN MANIFEST (optional but recommended):
#   If {run_tag}_manifest.json exists in OUTPUT_DIR (written there by the
#   simulation scripts when their results.output_dir points at the same
#   folder), its per-case metadata (uret, t_fault, t_clear, t_stop,
#   setpoints, status) is carried into the export manifest and failed cases
#   are skipped. If it does not exist, the exporter falls back to exporting
#   every study case found in the run folder, with a warning that case
#   metadata is unavailable — the CSVs are still complete.
#
# Output layout:
#
#   {OUTPUT_DIR}\
#     FRT_2026-07-09_14-30-54\          <- one folder per exported run
#       FRT_3PH_0.05_Q0.csv             <- one CSV per case
#       ...
#       export_manifest.json            <- case -> file + column mapping
#     FRT_2026-07-12_13-44-25\          <- second selected run, same layout
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
# If ONE selected run fails, the others still export; the summary reports
# per-run status.
#
# Usage:
#   1. Set STUDY ("FRT" or "FREQ") and OUTPUT_DIR below
#   2. Point a ComPython object at this script and run from PowerFactory
#
# Dependencies: gcc_common.py in the same folder as this script.
#
# Author  : GCC Automation Team
# Version : 2.0
#
# CHANGELOG 1.2 -> 2.0
# --------------------
#   * INDEPENDENT: no project_config.yaml, no PyYAML, no element names.
#     Output goes to the OUTPUT_DIR constant below.
#   * Exports ALL recorded variables of each result file by enumerating its
#     columns (ElmRes.GetNumberOfColumns / GetObject / GetVariable) instead
#     of a fixed RECORD_VARS list resolved via config element names.
#     BREAKING for the CSV header format: friendly labels
#     ("POC Pos-Seq Voltage [pu]") are replaced by self-describing
#     "{element} | {variable}" headers ("POC | m:u1"). The evaluation tool
#     must read the column mapping from export_manifest.json.
#   * Run manifest is now OPTIONAL: without it, all cases in the run folder
#     are exported and case metadata is marked unavailable.
#
# CHANGELOG 1.1 -> 1.2
# --------------------
#   * Multi-select run picker (ShowModalSelectBrowser), per-run error
#     isolation, combined summary.
# ==============================================================================

import sys
import os
import csv
import json
import time as clock

# ------------------------------------------------------------------------------
# Make gcc_common.py importable (it sits next to this script)
# ------------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcc_common as gcc

import powerfactory


# ------------------------------------------------------------------------------
# OUTPUT DIRECTORY — where the per-run export folders are written.
# Also where {run_tag}_manifest.json files are looked for (optional).
# ------------------------------------------------------------------------------
OUTPUT_DIR = r"C:\Users\SHIRED\Downloads"

# ------------------------------------------------------------------------------
# WHICH STUDY FAMILY TO EXPORT:  "FRT"  or  "FREQ"
#   Selects the run-folder prefix (FRT_* / FREQ_*).
# ------------------------------------------------------------------------------
STUDY = "FRT"

# ------------------------------------------------------------------------------
# WHICH RUN TO EXPORT
#   None            -> single run: exported directly; several runs: a
#                      selection dialog opens (multi-select supported)
#   "FRT_2026-..."  -> that specific run folder, no dialog (automation path)
# ------------------------------------------------------------------------------
RUN_TAG = None

if STUDY not in ("FRT", "FREQ"):
    raise RuntimeError(f"STUDY must be 'FRT' or 'FREQ', got '{STUDY}'")
RUN_PREFIX = STUDY

# Tolerance basis for the completeness check (only applied when the run
# manifest provides t_stop): last result time must reach t_stop - 2 x dtgrd_max.
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
# 2.  CASE LIST — manifest if available, folder contents otherwise
# ==============================================================================

def load_case_list(app, sc_folder, run_tag):
    """
    Build the list of cases to export for one run.

    Preferred source: {run_tag}_manifest.json in OUTPUT_DIR — carries full
    per-case metadata and solved/failed status.
    Fallback: every IntCase in the run folder, metadata unavailable.

    Returns (cases, manifest, manifest_path) — manifest/manifest_path are
    None in fallback mode.
    """
    manifest_path = os.path.join(OUTPUT_DIR, f"{run_tag}_manifest.json")

    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        cases = manifest.get("cases", [])
        if cases:
            return cases, manifest, manifest_path
        app.PrintWarn("  Run manifest contains no cases — falling back to "
                      "the run folder contents.")

    else:
        app.PrintWarn(
            f"  Run manifest not found in OUTPUT_DIR "
            f"({run_tag}_manifest.json) — exporting every study case in the "
            f"run folder. Case metadata (uret, t_fault, t_clear, setpoints, "
            f"solved/failed status) will be UNAVAILABLE in the export "
            f"manifest."
        )

    cases = [{"name": c.loc_name, "status": "unknown"}
             for c in sc_folder.GetContents("*.IntCase")]
    cases.sort(key=lambda c: c["name"])
    if not cases:
        raise RuntimeError("Run folder contains no study cases.")
    return cases, None, None


# ==============================================================================
# 3.  PER-CASE EXPORT — every recorded column, self-describing headers
# ==============================================================================

def enumerate_result_columns(elmRes):
    """
    Enumerate all columns of a loaded ElmRes.

    Returns a list of dicts:
      { "column": i, "element": loc_name, "class": class_name,
        "variable": var, "header": "element | variable" }
    Duplicate headers (two same-named elements recording the same variable)
    are disambiguated with the column index.
    """
    n_cols = elmRes.GetNumberOfColumns()
    cols, seen = [], {}
    for i in range(n_cols):
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
        if header in seen:                        # disambiguate duplicates
            header = f"{name} | {var} [{i}]"
        seen[header] = True
        cols.append({"column": i, "element": name, "class": cls,
                     "variable": var, "header": header})
    return cols


def export_case_all_columns(app, study_case, csv_path):
    """
    Export ONE study case's result file to ONE CSV — every recorded column.

    CSV format:  time_s, <one column per recorded variable>
    NOTE: NON-UNIFORM time base under iopt_adapt=1 — interpolate on time_s.

    Returns (n_rows, last_t, columns) — columns as from
    enumerate_result_columns(); last_t is None if no data.
    """
    study_case.Activate()
    comInc = app.GetFromStudyCase("ComInc")
    elmRes = comInc.p_resvar

    elmRes.Load()
    try:
        n_rows = elmRes.GetNumberOfRows()
        if n_rows == 0:
            return 0, None, []

        columns = enumerate_result_columns(elmRes)
        if not columns:
            return 0, None, []

        last_t = None
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["time_s"] + [c["header"] for c in columns])

            for i in range(n_rows):
                err_t, t = elmRes.GetValue(i)     # col omitted -> time column
                if err_t:
                    continue
                last_t = t

                row = [round(t, 6)]
                for c in columns:
                    err_v, val = elmRes.GetValue(i, c["column"])
                    row.append("" if err_v else repr(round(val, 10)))
                writer.writerow(row)

        return n_rows, last_t, columns

    finally:
        try:
            elmRes.Release()
        except Exception:
            pass


# ==============================================================================
# 4.  PER-RUN EXPORT
# ==============================================================================

def export_run(app, sc_folder):
    """
    Export every exportable case of ONE run folder to per-case CSVs plus an
    export_manifest.json.

    Returns a summary dict; raises RuntimeError if the run cannot be
    exported at all — the caller isolates that per run.
    """
    run_tag = sc_folder.loc_name
    app.PrintPlain(f"\n--- Exporting run '{run_tag}' ---")

    cases, manifest, manifest_path = load_case_list(app, sc_folder, run_tag)
    n_solved = sum(1 for c in cases if c.get("status") in ("solved", "unknown"))
    app.PrintPlain(f"  Cases    : {len(cases)} listed, {n_solved} to export"
                   + ("" if manifest else "  [no run manifest — metadata unavailable]"))

    export_dir = os.path.join(OUTPUT_DIR, run_tag)
    os.makedirs(export_dir, exist_ok=True)

    export_entries = []
    run_columns    = None      # column mapping of the first exported case
    n_ok, n_skip, n_err = 0, 0, 0

    for case in cases:
        name   = case["name"]
        status = case.get("status", "unknown")
        entry  = dict(case)              # carry ALL available metadata through

        if status == "failed":
            app.PrintWarn(f"  [{name}] skipped — failed in simulation "
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
            n_rows, last_t, columns = export_case_all_columns(
                app, matches[0], csv_path
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

        if run_columns is None:
            run_columns = columns

        # Completeness note — only when the manifest provided t_stop
        t_stop = case.get("t_stop")
        if t_stop is not None:
            complete = last_t >= (float(t_stop) - 2.0 * DTGRD_MAX_S)
            entry["complete"] = complete
            if not complete:
                app.PrintWarn(
                    f"  [{name}] result ends at t={last_t:.3f}s of "
                    f"{float(t_stop):.1f}s — exported, but case looks "
                    f"incomplete."
                )

        entry["csv_file"]      = fname          # relative to export_dir
        entry["export_status"] = "ok"
        entry["n_rows"]        = n_rows
        entry["t_end_s"]       = round(last_t, 6)
        entry["n_columns"]     = len(columns)
        export_entries.append(entry)
        n_ok += 1
        app.PrintPlain(f"  [{name}] -> {fname}  ({n_rows} rows x "
                       f"{len(columns)} vars, t_end={last_t:.3f}s)")

    # --------------------------------------------------------------------------
    # Export manifest — the evaluation tool's entry point for this run.
    # 'columns' is the authoritative column mapping (element/class/variable
    # per CSV header) — the evaluator reads this, never header strings.
    # --------------------------------------------------------------------------
    export_manifest = {
        "run_tag":          run_tag,
        "study":            STUDY,
        "exported":         gcc.datetime.now().isoformat(timespec="seconds"),
        "source_manifest":  manifest_path,     # None in fallback mode
        "export_dir":       export_dir,
        "columns":          run_columns or [],
        "time_base_note":   ("NON-UNIFORM time base (iopt_adapt=1): "
                             "interpolate on time_s, never index by row."),
        "project":          (manifest or {}).get("project"),
        "plant":            (manifest or {}).get("plant"),
        "grid_code_file":   (manifest or {}).get("grid_code_file"),
        "grid_code_meta":   (manifest or {}).get("grid_code_meta"),
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
        "had_manifest":  manifest is not None,
        "n_total":       len(cases),
        "n_ok":          n_ok,
        "n_skip":        n_skip,
        "n_err":         n_err,
    }


# ==============================================================================
# 5.  MAIN
# ==============================================================================

def _run(app):

    app.PrintPlain("=" * 68)
    app.PrintPlain(f"  GCC Results Export ({STUDY}) v2.0 — starting")
    app.PrintPlain("=" * 68)
    t0 = clock.perf_counter()

    # --------------------------------------------------------------------------
    # 5.1  Output directory
    # --------------------------------------------------------------------------
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        probe = os.path.join(OUTPUT_DIR, ".gcc_write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        raise RuntimeError(f"OUTPUT_DIR is not writable: {OUTPUT_DIR}\n{e}")
    app.PrintPlain(f"\nOutput   : {OUTPUT_DIR}")

    # --------------------------------------------------------------------------
    # 5.2  Select the run(s) to export
    # --------------------------------------------------------------------------
    sc_folder_root = gcc.get_folder(app, "study")
    run_folders    = select_run_folders(app, sc_folder_root)
    app.PrintPlain(f"Selected : {[f.loc_name for f in run_folders]}")

    # --------------------------------------------------------------------------
    # 5.3  Export each selected run — one failure never stops the others
    # --------------------------------------------------------------------------
    summaries  = []
    run_errors = {}   # {run_tag: reason}

    for sc_folder in run_folders:
        try:
            summaries.append(export_run(app, sc_folder))
        except Exception as e:
            run_errors[sc_folder.loc_name] = str(e)
            app.PrintWarn(f"  Run '{sc_folder.loc_name}' NOT exported: {e}")

    # --------------------------------------------------------------------------
    # 5.4  Combined summary
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
        if not s["had_manifest"]:
            line += "  [no run manifest]"
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
