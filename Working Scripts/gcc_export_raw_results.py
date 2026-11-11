# ==============================================================================
# GCC — Raw Results Export (stand-alone)
# ==============================================================================
# Exports the simulation results of every study case in ONE study case folder
# to CSV files: one CSV per study case, with every variable recorded in the
# case's result file. Needs no other script and no config file.
#
# Output:  <OUTPUT_DIR>\<STUDY_CASE_FOLDER>\
#            FRT_3PH_0.05_Q0.csv        one CSV per study case
#            ...
#            export_manifest.json       case -> file, rows, end time, columns
#
# CSV format:
#   time_s, then one column per recorded variable, headed
#   "<element> | <variable>", e.g. "POC Breaker | m:Psum:bus1".
#   NOTE: with adaptive step size the time step is not uniform — interpolate
#   on time_s, never assume a fixed row interval.
#
# Cases without simulation results are skipped and listed at the end.
#
# Usage:
#   1. Set OUTPUT_DIR and STUDY_CASE_FOLDER below
#   2. Point a ComPython object at this script and run it from PowerFactory
# ==============================================================================

import os
import csv
import json
from datetime import datetime

import powerfactory


# ------------------------------------------------------------------------------
# SETTINGS
# ------------------------------------------------------------------------------
# Folder the CSVs are written to (a subfolder named after the study case
# folder is created inside it)
OUTPUT_DIR = r"C:\Users\SHIRED\Downloads\Results Export\Raw Results"

# Name of the study case folder to export, as shown under Study Cases,
# e.g. "FRT_2026-11-10_09-12-44". Searched for anywhere under Study Cases.
STUDY_CASE_FOLDER = "FRT_2026-11-10_09-12-44"


# ==============================================================================

def find_folder(app, name):
    """The IntFolder called `name` anywhere under Study Cases."""
    study = app.GetProjectFolder("study")
    found = [f for f in study.GetContents("*.IntFolder", 1) if f.loc_name == name]
    if not found:
        raise RuntimeError(f"Study case folder '{name}' not found under Study "
                           f"Cases. Check STUDY_CASE_FOLDER.")
    if len(found) > 1:
        app.PrintWarn(f"  {len(found)} folders named '{name}' found — exporting "
                      f"the first one.")
    return found[0]


def result_columns(elmRes):
    """[(column index, header, element, class, variable), ...] of a loaded ElmRes."""
    cols, seen = [], set()
    for i in range(elmRes.GetNumberOfColumns()):
        obj, var = elmRes.GetObject(i), elmRes.GetVariable(i)
        if not var:
            continue
        elm    = obj.loc_name if obj is not None else "?"
        cls    = obj.GetClassName() if obj is not None else "?"
        header = f"{elm} | {var}"
        if header in seen:                 # same name twice: add the column index
            header = f"{header} [{i}]"
        seen.add(header)
        cols.append((i, header, elm, cls, var))
    return cols


def export_case(app, case, csv_path):
    """
    Write one study case's result file to csv_path.
    Returns (n_rows, t_end, columns) — n_rows 0 if there are no results.
    """
    case.Activate()
    elmRes = app.GetFromStudyCase("ComInc").p_resvar
    if elmRes is None:
        return 0, None, []

    elmRes.Load()
    try:
        n_rows = elmRes.GetNumberOfRows()
        if n_rows == 0:
            return 0, None, []
        cols = result_columns(elmRes)

        t_end = None
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["time_s"] + [c[1] for c in cols])
            for row in range(n_rows):
                err, t = elmRes.GetValue(row, -1)      # -1 = time column
                if err:
                    continue
                values = []
                for col, *_ in cols:
                    err, v = elmRes.GetValue(row, col)
                    values.append("" if err else repr(round(v, 10)))
                writer.writerow([round(t, 6)] + values)
                t_end = t
        return n_rows, t_end, cols
    finally:
        elmRes.Release()


def main():
    app = powerfactory.GetApplication()
    app.ClearOutputWindow()

    folder  = find_folder(app, STUDY_CASE_FOLDER)
    out_dir = os.path.join(OUTPUT_DIR, folder.loc_name)
    os.makedirs(out_dir, exist_ok=True)
    cases = sorted(folder.GetContents("*.IntCase", 1), key=lambda c: c.loc_name)

    app.PrintPlain("=" * 72)
    app.PrintPlain("  GCC — Raw Results Export")
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  Study case folder : {folder.loc_name}   ({len(cases)} cases)")
    app.PrintPlain(f"  Output            : {out_dir}\n")

    entries, no_results, failed = [], [], []
    app.EchoOff()
    try:
        for case in cases:
            name  = case.loc_name
            fname = name.replace(" ", "_") + ".csv"
            path  = os.path.join(out_dir, fname)
            try:
                n_rows, t_end, cols = export_case(app, case, path)
            except Exception as exc:
                failed.append((name, str(exc)))
                continue
            if n_rows == 0:
                no_results.append(name)
                continue
            entries.append({
                "case": name, "csv_file": fname, "n_rows": n_rows,
                "t_end_s": round(t_end, 6) if t_end is not None else None,
                "columns": [{"header": h, "element": e, "class": c, "variable": v}
                            for _, h, e, c, v in cols],
            })
            app.PrintPlain(f"  {name:28s} -> {fname}  ({n_rows} rows, "
                           f"{len(cols)} variables, t_end = {t_end:.3f} s)")
    finally:
        case_now = app.GetActiveStudyCase()
        if case_now is not None:
            case_now.Deactivate()
        app.EchoOn()

    manifest = {
        "study_case_folder": folder.loc_name,
        "exported":          datetime.now().isoformat(timespec="seconds"),
        "output_dir":        out_dir,
        "time_base_note":    "Time step may be non-uniform: interpolate on time_s.",
        "cases":             entries,
        "skipped_no_results": no_results,
        "failed":            [{"case": n, "error": e} for n, e in failed],
    }
    with open(os.path.join(out_dir, "export_manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    app.PrintPlain("")
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  {len(entries)} case(s) exported to {out_dir}")
    if no_results:
        app.PrintPlain(f"  Skipped, no simulation results ({len(no_results)}): "
                       + ", ".join(no_results))
    for name, error in failed:
        app.PrintWarn(f"  FAILED  {name}: {error}")
    app.PrintPlain("=" * 72)


main()
