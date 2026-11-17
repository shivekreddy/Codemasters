# ==============================================================================
# GCC FRT: POC line-to-line voltages (existing runs)
# ==============================================================================
# In a phase-to-phase (2PH) fault the faulted line-to-line voltage drops to the
# retained voltage, while the faulted phases to ground stay near 0.5 pu. The
# POC page's top-left graph of runs made before frt_1 v5.10 shows the
# line-to-ground voltages (m:u:A/B/C), so a 2PH dip to 0.05 pu cannot be seen.
#
# For each selected case this script
#   * adds m:ul:A, m:ul:B, m:ul:C (line-to-line AB, BC, CA) of the POC busbar
#     to the case's result object (recorded from the next simulation on);
#   * switches the POC page's top-left graph ('Curve plot') to them, keeping
#     colours, line styles and widths; legend labels become
#     "<POC name> Line-Line Voltage AB (p.u.)" etc.
#
# The existing results do not contain m:ul, so RE-RUN THE SIMULATIONS
# afterwards (frt_1 with setup_only: false, run_sim_only: true), then export.
# UNDO = True switches the graph back to m:u:A/B/C. DRY_RUN = True only reports.
#
# Usage: point a ComPython object at this script and run it from PowerFactory.
# Dependencies: gcc_common.py and the project config in Working Scripts (one
# folder up), from the SAME version as this script.
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
DRY_RUN = True     # True = report only, change nothing
UNDO    = False    # True = switch the graph back to line-to-ground (m:u:A/B/C)

# Cases. Names / report names, wildcards ok. Default: all 2PH cases.
# None = every case of the run.
CASES = ["*2PH*"]

# Run folder name in Study Cases, e.g. "FRT_2026-11-11_13-56-23 (PPC Enabled)".
# None = paths.frt_run from the project config, else the latest FRT_* run.
RUN_FOLDER = None


# ==============================================================================

GRAPH = "Curve plot"
LG = ("m:u:A", "m:u:B", "m:u:C")
LL = ("m:ul:A", "m:ul:B", "m:ul:C")
STYLE = ("curveTableColor", "curveTableLineStyle", "curveTableLineWidth")


def vec(ds, attr):
    try:
        return list(ds.GetAttribute(attr) or [])
    except Exception:
        return []


def find_run_folder(study, name):
    found = [f for f in study.GetContents("*.IntFolder", 1) if f.loc_name == name]
    if not found:
        raise RuntimeError(f"Run folder '{name}' not found under Study Cases.")
    return found[0]


def switch_graph(ds, src, dst, label_name):
    """Rebuild the curve table with dst variables in place of src (same order,
    style kept). Returns (bus element, number of curves switched)."""
    elms = vec(ds, "curveTableElement")
    vars_ = [str(v) for v in vec(ds, "curveTableVariable")]
    styles = {a: vec(ds, a) for a in STYLE}
    n = len(vars_)
    new_vars = [dst[src.index(v)] if v in src else v for v in vars_]
    for i in range(n - 1, -1, -1):
        ds.RemoveCurve(i)
    for elm, var in zip(elms, new_vars):
        ds.AddCurve(elm, var)
    for attr, values in styles.items():
        if len(values) == n:
            try:
                ds.SetAttribute(attr, values)
            except Exception:
                pass
    try:
        ds.SetAttribute(gcc.CURVE_LABEL_ATTR,
                        [gcc.curve_label(label_name, v) for v in new_vars])
    except Exception:
        pass
    bus = next((e for e, v in zip(elms, vars_) if v in src), None)
    return bus, sum(v in src for v in vars_)


def main():
    app = powerfactory.GetApplication()
    app.ClearOutputWindow()
    mode = "DRY RUN: nothing changed" if DRY_RUN else ("UNDO" if UNDO else "WRITING")
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  GCC FRT: POC line-to-line voltages   ({mode})")
    app.PrintPlain("=" * 72)

    cfg = gcc.load_yaml(gcc.resolve_config_path(__file__))
    label_name = gcc.plot_label_names(cfg)["poc_busbar"]
    study = gcc.get_folder(app, "study")
    run = RUN_FOLDER or gcc.run_name_setting(cfg, "FRT")
    folder = find_run_folder(study, run) if run else gcc.find_latest_run_folder(study, "FRT")
    if folder is None:
        app.PrintError("No FRT_* run folder in Study Cases.")
        return
    cases = sorted(folder.GetContents("*.IntCase", 1), key=lambda c: c.loc_name)
    if CASES:
        cases, unmatched = gcc.select_cases(cases, CASES)
        for pat in unmatched:
            app.PrintWarn(f"  No case matches '{pat}'")
    src, dst = (LL, LG) if UNDO else (LG, LL)
    app.PrintPlain(f"  Run folder : {folder.loc_name}   ({len(cases)} case(s))")
    app.PrintPlain(f"  POC '{GRAPH}': {', '.join(src)}  ->  {', '.join(dst)}\n")

    n_changed, problems = 0, []
    app.EchoOff()
    try:
        for case in cases:
            case.Activate()
            board = app.GetGraphicsBoard()
            pages = board.GetContents("POC.GrpPage") if board else []
            plots = pages[0].GetContents(f"{GRAPH}.PltLinebarplot") if pages else []
            if not plots:
                problems.append(f"{case.loc_name}: no '{GRAPH}' on a 'POC' page")
                continue
            ds = plots[0].GetDataSeries()
            vars_ = [str(v) for v in vec(ds, "curveTableVariable")]
            if not any(v in src for v in vars_):
                msg = ("already line-to-line" if not UNDO and any(v in LL for v in vars_)
                       else "nothing to switch")
                app.PrintPlain(f"  {case.loc_name:30s} {msg}")
                continue
            if DRY_RUN:
                n_changed += 1
                app.PrintPlain(f"  {case.loc_name:30s} would be switched")
                continue
            try:
                bus, n = switch_graph(ds, src, dst, label_name)
                recorded = ""
                if not UNDO:
                    elm_res = app.GetFromStudyCase("ComInc").p_resvar
                    if elm_res is None or bus is None:
                        problems.append(f"{case.loc_name}: no result object; "
                                        "m:ul not added to the recording")
                    else:
                        for v in LL:
                            elm_res.AddVariable(bus, v)
                        recorded = f", m:ul:A/B/C recorded on {bus.loc_name}"
                n_changed += 1
                app.PrintPlain(f"  {case.loc_name:30s} {n} curve(s) switched{recorded}")
            except Exception as exc:
                problems.append(f"{case.loc_name}: {exc}")
    finally:
        active = app.GetActiveStudyCase()
        if active is not None:
            active.Deactivate()
        app.EchoOn()

    app.PrintPlain("")
    app.PrintPlain("=" * 72)
    verb = "would change" if DRY_RUN else "changed"
    app.PrintPlain(f"  {n_changed} case(s) {verb} of {len(cases)}")
    for p in problems:
        app.PrintWarn(f"  {p}")
    if n_changed and not DRY_RUN and not UNDO:
        app.PrintPlain("  Next: re-run these cases (frt_1, run_sim_only: true) so "
                       "m:ul is in the results.")
    app.PrintPlain("=" * 72)


main()
