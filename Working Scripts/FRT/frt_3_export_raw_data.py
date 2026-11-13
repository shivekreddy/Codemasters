# ==============================================================================
# GCC — Raw Results Export per Plot (FRT)
# ==============================================================================
# Exports the simulation results of every study case in ONE study case folder,
# split the same way as the FRT plot pages: one folder per study case, and in
# it one CSV per plot page holding exactly that page's variables.
#
# Output:  <paths.root>\FRT Raw Data\<FRT run folder>\
#            FRT 1PH 0.05 Q0\
#              POC.csv               POC busbar voltages + POC breaker P/Q/I
#              GU Terminal.csv       one plant:  GU busbar + inverter
#              (GU Terminal 1.csv    two plants: plant 1
#               GU Terminal 2.csv                plant 2)
#            FRT 1PH 0.05 Qmax\
#              ...
#            export_manifest.json    cases, files, columns, missing variables
#
# The pages and their elements come from the PROJECT CONFIG (elements +
# plot_labels): plant 2 configured (gu_busbar2 / inverter2) -> two GU pages.
# Column headers match the plot legends: "<name> <variable> (<unit>)",
# e.g. "Inverter 1 Total Active Power (MW)", plus time_s first.
# NOTE: with adaptive step size the time step is not uniform — interpolate on
# time_s, never assume a fixed row interval.
#
# Cases without simulation results are skipped and listed at the end. A
# variable that is not in a case's result file is left blank and listed.
#
# Which run and where to: from the project config next to this script
# (paths.root; the latest FRT_* run, or paths.frt_run if set). Nothing to set
# in this script.
#
# Usage: point a ComPython object at this script and run it from PowerFactory.
# Dependencies: PyYAML, gcc_common.py in the same folder.
# ==============================================================================

import os
import csv
import json
from datetime import datetime

import sys

import yaml
import powerfactory

# gcc_common.py and the project config live one folder up (Working Scripts)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.pop("gcc_common", None)      # always load gcc_common.py fresh from this folder
import gcc_common as gcc                 # noqa: E402


# ------------------------------------------------------------------------------
# PAGE CONTENT — the same variables as the FRT plot pages
# ------------------------------------------------------------------------------
BUSBAR_VARS = ["m:u:A", "m:u:B", "m:u:C", "m:u1", "m:u2"]
SOURCE_VARS = ["m:Psum:bus1", "m:Qsum:bus1", "m:i1P:bus1", "m:i1Q:bus1",
               "m:i2Q:bus1"]

VARIABLE_LABELS = {
    "m:u:A":       ("Line-Ground Voltage A",              "p.u."),
    "m:u:B":       ("Line-Ground Voltage B",              "p.u."),
    "m:u:C":       ("Line-Ground Voltage C",              "p.u."),
    "m:u1":        ("Positive-Sequence Voltage",          "p.u."),
    "m:u2":        ("Negative-Sequence Voltage",          "p.u."),
    "m:Psum:bus1": ("Total Active Power",                 "MW"),
    "m:Qsum:bus1": ("Total Reactive Power",               "Mvar"),
    "m:i1P:bus1":  ("Positive-Sequence Active Current",   "p.u."),
    "m:i1Q:bus1":  ("Positive-Sequence Reactive Current", "p.u."),
    "m:i2Q:bus1":  ("Negative-Sequence Reactive Current", "p.u."),
}

# PowerFactory classes per element role
BUSBAR_CLASSES = ("ElmTerm",)
BREAKER_CLASSES = ("ElmCoup",)
INVERTER_CLASSES = ("ElmGenstat", "ElmSym")

LABEL_DEFAULTS = {"poc_busbar": "POC", "poc_breaker": "POC",
                  "gu_busbar1": "GU 1", "inverter1": "Inverter 1",
                  "gu_busbar2": "GU 2", "inverter2": "Inverter 2"}


# ==============================================================================
# 1.  PAGES FROM THE PROJECT CONFIG
# ==============================================================================

def load_pages(config_file):
    """
    The plot pages per study case, from the project config:
      [(page name, [(element loc_name, classes, legend name, variable), ...])]
    """
    if not os.path.isfile(config_file):
        raise RuntimeError(f"Project config not found:\n  {config_file}\n"
                           "Put project_config_update.yaml next to this script.")
    with open(config_file, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    elem = cfg.get("elements")
    if not isinstance(elem, dict):
        raise RuntimeError(f"No top-level 'elements' section in:\n  {config_file}")
    given  = cfg.get("plot_labels") or {}
    labels = {k: str(given.get(k) or d) for k, d in LABEL_DEFAULTS.items()}

    def name(*keys):                      # first configured key wins
        for k in keys:
            if elem.get(k):
                return str(elem[k])
        return None

    def page(bus, bus_label, src, src_cls, src_label):
        return ([(bus, BUSBAR_CLASSES, bus_label, v) for v in BUSBAR_VARS] +
                [(src, src_cls, src_label, v) for v in SOURCE_VARS])

    poc_bb, poc_brk = name("poc_busbar"), name("poc_breaker")
    if not poc_bb or not poc_brk:
        raise RuntimeError("Set elements.poc_busbar and elements.poc_breaker "
                           "in the project config.")
    pages = [("POC", page(poc_bb, labels["poc_busbar"], poc_brk,
                          BREAKER_CLASSES, labels["poc_breaker"]))]

    plants = []
    for n, gu_keys, inv_keys in ((1, ("gu_busbar1", "gu_busbar"), ("inverter1", "inverter")),
                                 (2, ("gu_busbar2",), ("inverter2",))):
        gu, inv = name(*gu_keys), name(*inv_keys)
        if gu and inv:
            plants.append((n, gu, inv))
        elif gu or inv:
            raise RuntimeError(f"Plant {n}: set both gu_busbar{n} and inverter{n} "
                               f"in the project config (or both to null).")
    for n, gu, inv in plants:
        page_name = "GU Terminal" if len(plants) == 1 else f"GU Terminal {n}"
        pages.append((page_name, page(gu, labels[f"gu_busbar{n}"], inv,
                                      INVERTER_CLASSES, labels[f"inverter{n}"])))
    return pages


def header(legend_name, variable):
    desc, unit = VARIABLE_LABELS.get(variable, (variable, ""))
    return f"{legend_name} {desc}" + (f" ({unit})" if unit else "")


# ==============================================================================
# 2.  EXPORT
# ==============================================================================

def find_folder(app, name):
    """The IntFolder called `name` anywhere under Study Cases."""
    study = app.GetProjectFolder("study")
    found = [f for f in study.GetContents("*.IntFolder", 1) if f.loc_name == name]
    if not found:
        raise RuntimeError(f"Study case folder '{name}' not found under Study "
                           f"Cases. Check paths.frt_run in the project config.")
    if len(found) > 1:
        app.PrintWarn(f"  {len(found)} folders named '{name}' found — exporting "
                      f"the first one.")
    return found[0]


def column_index(elmRes):
    """{(LOC_NAME, class, variable): column} for every recorded variable."""
    index = {}
    for i in range(elmRes.GetNumberOfColumns()):
        obj, var = elmRes.GetObject(i), elmRes.GetVariable(i)
        if obj is not None and var:
            index.setdefault((obj.loc_name.upper(), obj.GetClassName(), var), i)
    return index


def export_case(app, case, case_dir, pages):
    """
    Write one CSV per page for one study case.
    Returns (n_rows, t_end, files, missing) — n_rows 0 if there are no results.
    """
    case.Activate()
    elmRes = app.GetFromStudyCase("ComInc").p_resvar
    if elmRes is None:
        return 0, None, [], []

    elmRes.Load()
    try:
        n_rows = elmRes.GetNumberOfRows()
        if n_rows == 0:
            return 0, None, [], []
        index = column_index(elmRes)

        # time column once, shared by all pages of this case
        times = []
        for row in range(n_rows):
            err, t = elmRes.GetValue(row, -1)          # -1 = time column
            times.append(None if err else t)

        os.makedirs(case_dir, exist_ok=True)
        files, missing = [], []
        for page_name, curves in pages:
            cols, heads = [], []
            for elm, classes, legend, var in curves:
                col = next((index[(elm.upper(), cls, var)] for cls in classes
                            if (elm.upper(), cls, var) in index), None)
                if col is None:
                    missing.append(f"{page_name}: {elm} {var}")
                cols.append(col)
                heads.append(header(legend, var))

            fname = f"{page_name}.csv"
            with open(os.path.join(case_dir, fname), "w", newline="",
                      encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["time_s"] + heads)
                for row, t in enumerate(times):
                    if t is None:
                        continue
                    values = []
                    for col in cols:
                        if col is None:
                            values.append("")
                            continue
                        err, v = elmRes.GetValue(row, col)
                        values.append("" if err else repr(round(v, 10)))
                    writer.writerow([round(t, 6)] + values)
            files.append({"page": page_name, "csv_file": fname, "columns": heads})

        t_end = next((t for t in reversed(times) if t is not None), None)
        return n_rows, t_end, files, missing
    finally:
        elmRes.Release()


def main():
    app = powerfactory.GetApplication()
    app.ClearOutputWindow()

    config_file = gcc.resolve_config_path(__file__)
    cfg     = gcc.load_yaml(config_file)
    pages   = load_pages(config_file)
    run     = gcc.run_name_setting(cfg, "FRT")
    if run:
        folder = find_folder(app, run)
    else:
        folder = gcc.find_latest_run_folder(app.GetProjectFolder("study"), "FRT")
        if folder is None:
            raise RuntimeError("No FRT_* run folder in Study Cases. Run frt_1_create_and_run_cases.py first.")
    out_dir = gcc.results_dir(cfg, "frt_raw", folder.loc_name)
    cases = sorted(folder.GetContents("*.IntCase", 1), key=lambda c: c.loc_name)

    app.PrintPlain("=" * 72)
    app.PrintPlain("  GCC — Raw Results Export per Plot")
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  Study case folder : {folder.loc_name}   ({len(cases)} cases)")
    app.PrintPlain(f"  Plots per case    : {', '.join(p for p, _ in pages)}")
    app.PrintPlain(f"  Output            : {out_dir}\n")

    entries, no_results, failed, all_missing = [], [], [], {}
    app.EchoOff()
    try:
        for case in cases:
            name = case.loc_name
            try:
                n_rows, t_end, files, missing = export_case(
                    app, case, os.path.join(out_dir, name), pages)
            except Exception as exc:
                failed.append((name, str(exc)))
                continue
            if n_rows == 0:
                no_results.append(name)
                continue
            entries.append({"case": name, "folder": name, "n_rows": n_rows,
                            "t_end_s": round(t_end, 6) if t_end is not None else None,
                            "files": files, "missing_variables": missing})
            if missing:
                all_missing[name] = missing
            app.PrintPlain(f"  {name:28s} -> {len(files)} CSV(s), {n_rows} rows, "
                           f"t_end = {t_end:.3f} s"
                           + (f", {len(missing)} variable(s) missing" if missing else ""))
    finally:
        case_now = app.GetActiveStudyCase()
        if case_now is not None:
            case_now.Deactivate()
        app.EchoOn()

    manifest = {
        "study_case_folder":  folder.loc_name,
        "exported":           datetime.now().isoformat(timespec="seconds"),
        "project_config":     config_file,
        "output_dir":         out_dir,
        "pages":              [p for p, _ in pages],
        "time_base_note":     "Time step may be non-uniform: interpolate on time_s.",
        "cases":              entries,
        "skipped_no_results": no_results,
        "failed":             [{"case": n, "error": e} for n, e in failed],
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
    for name, missing in all_missing.items():
        app.PrintWarn(f"  Not in the result file of {name} (left blank): "
                      + "; ".join(missing))
    for name, error in failed:
        app.PrintWarn(f"  FAILED  {name}: {error}")
    app.PrintPlain("=" * 72)


main()
