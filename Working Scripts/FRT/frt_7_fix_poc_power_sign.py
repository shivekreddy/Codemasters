# ==============================================================================
# GCC FRT: Fix POC Power Sign (older runs)
# ==============================================================================
# For FRT runs made before frt_1 v5.6: their POC page plots active and
# reactive power on the POC breaker's bus1 side (load oriented, export
# negative). This script switches the curves of that ONE graph (POC page,
# bottom left, 'Curve plot(2)') to the bus2 side (export positive), as the
# current frt_1 does. Nothing else is changed; colours, line styles and
# labels are kept.
#
# A curve can only show bus2 if the case's result file holds it. Older runs
# usually did not record bus2, so per case the script:
#   1. adds m:Psum:bus2 / m:Qsum:bus2 of the POC breaker to the case's result
#      object (recorded from the next simulation of the case on);
#   2. switches the curves to bus2 if the results already hold them;
#      otherwise it reports "rerun needed" and leaves the curves on bus1.
# Rerun those cases (frt_1 with run_sim_only: true), then run this script
# again to switch them.
#
# DRY_RUN = True (default) only reports, per case: the curves on the graph,
# the pre-fault P and Q on bus1 from the results, and whether bus2 is
# recorded. Check that list before setting DRY_RUN = False.
#
# Usage: point a ComPython object at this script and run it from PowerFactory.
# Dependencies: gcc_common.py and the project config in Working Scripts (one
# folder up).
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
DRY_RUN = True     # True = report only; False = add bus2 and switch the curves

# Cases. None = every case of the run; or names / report names, wildcards ok
CASES = None

# Run folder in Study Cases. None = paths.frt_run, else the latest FRT_* run.
RUN_FOLDER = None


# ==============================================================================

PAGE, GRAPH = "POC", "Curve plot(2)"
OLD_TO_NEW = {"m:Psum:bus1": "m:Psum:bus2", "m:Qsum:bus1": "m:Qsum:bus2"}
DEFAULT_VARS = ["m:Psum:bus1", "m:Qsum:bus1"]          # as frt_1 created them
STYLE_ATTRS = ("curveTableColor", "curveTableLineStyle", "curveTableLineWidth",
               gcc.CURVE_LABEL_ATTR)


def _get(obj, attr):
    try:
        return getattr(obj, attr)
    except Exception:
        return None


def curve_table(ds):
    """(elements, variables) of the data series, or (None, None) if not readable."""
    elms, vars_ = _get(ds, "curveTableElement"), _get(ds, "curveTableVariable")
    if isinstance(vars_, (list, tuple)) and vars_:
        return list(elms or []), [str(v) for v in vars_]
    return None, None


def pre_fault(elmRes, brk, var, t_pre):
    """Value of brk:var at the last sample before t_pre, or None."""
    col = elmRes.FindColumn(brk, var)
    if col < 0:
        return None
    last = None
    for row in range(elmRes.GetNumberOfRows()):
        err, t = elmRes.GetValue(row, -1)
        if err or t >= t_pre:
            break
        err, v = elmRes.GetValue(row, col)
        if not err:
            last = v
    return last


def main():
    app = powerfactory.GetApplication()
    app.ClearOutputWindow()
    app.PrintPlain("=" * 72)
    app.PrintPlain("  GCC FRT: Fix POC Power Sign   " + ("(DRY RUN: nothing changed)"
                                                       if DRY_RUN else "(WRITING)"))
    app.PrintPlain("=" * 72)

    cfg = gcc.load_yaml(gcc.resolve_config_path(__file__))
    brk = gcc.find_element(app, "ElmCoup", (cfg.get("elements") or {}).get("poc_breaker"))
    t_pre = float((cfg.get("simulation") or {}).get("fault_start_s", 10.0)) - 0.5
    run = RUN_FOLDER or gcc.run_name_setting(cfg, "FRT")
    folder = (gcc.find_run_folder_any(app, run) if run
              else gcc.find_latest_run_folder(gcc.get_folder(app, "study"), "FRT"))
    if folder is None:
        app.PrintError("No FRT_* run folder in Study Cases.")
        return
    cases = sorted(folder.GetContents("*.IntCase", 1), key=lambda c: c.loc_name)
    if CASES:
        cases, unmatched = gcc.select_cases(cases, CASES)
        for pat in unmatched:
            app.PrintWarn(f"  No case matches '{pat}'")
    app.PrintPlain(f"  Run folder  : {folder.loc_name}   ({len(cases)} case(s))")
    app.PrintPlain(f"  POC breaker : {brk.loc_name}")
    app.PrintPlain(f"  Pre-fault   : last sample before {t_pre:g} s\n")

    switched, rerun, already, problems = [], [], [], []
    app.EchoOff()
    try:
        for case in cases:
            name = case.loc_name
            case.Activate()
            board = app.GetGraphicsBoard()
            pages = board.GetContents(f"{PAGE}.GrpPage") if board else []
            plots = pages[0].GetContents(f"{GRAPH}.PltLinebarplot") if pages else []
            if not plots:
                problems.append(f"{name}: no '{GRAPH}' on a '{PAGE}' page")
                continue
            ds = plots[0].GetDataSeries()
            elms, vars_ = curve_table(ds)
            if vars_ is None:
                vars_ = list(DEFAULT_VARS)     # table not readable: frt_1's layout
                elms = [brk] * len(vars_)
            if not any(v in OLD_TO_NEW for v in vars_):
                already.append(name)
                app.PrintPlain(f"  {name:24s} curves {vars_}: nothing to switch")
                continue

            elmRes = app.GetFromStudyCase("ComInc").p_resvar
            if elmRes is None:
                problems.append(f"{name}: no result object")
                continue
            if not DRY_RUN:
                for new in OLD_TO_NEW.values():
                    elmRes.AddVariable(brk, new)          # recorded from the next run on
            elmRes.Load()
            try:
                has_rows = elmRes.GetNumberOfRows() > 0
                p1 = pre_fault(elmRes, brk, "m:Psum:bus1", t_pre) if has_rows else None
                q1 = pre_fault(elmRes, brk, "m:Qsum:bus1", t_pre) if has_rows else None
                have_bus2 = has_rows and all(elmRes.FindColumn(brk, v) >= 0
                                             for v in OLD_TO_NEW.values())
            finally:
                elmRes.Release()

            fmt = lambda x: "-" if x is None else f"{x:.2f}"
            state = "bus2 recorded" if have_bus2 else "bus2 NOT recorded (rerun needed)"
            app.PrintPlain(f"  {name:24s} pre-fault bus1 P {fmt(p1)} MW, Q {fmt(q1)} Mvar; "
                           f"{state}")
            if not have_bus2:
                rerun.append(name)
                continue
            if DRY_RUN:
                switched.append(name)
                continue

            style = {a: _get(ds, a) for a in STYLE_ATTRS}
            new_vars = [OLD_TO_NEW.get(v, v) for v in vars_]
            ds.ClearCurves()
            for elm, var in zip(elms, new_vars):
                ds.AddCurve(elm if elm is not None else brk, var)
            for attr, val in style.items():         # keep colours, styles, labels
                if isinstance(val, (list, tuple)) and len(val) == len(new_vars):
                    try:
                        setattr(ds, attr, list(val))
                    except Exception:
                        pass
            switched.append(name)
    finally:
        active = app.GetActiveStudyCase()
        if active is not None:
            active.Deactivate()
        app.EchoOn()

    app.PrintPlain("")
    app.PrintPlain("=" * 72)
    verb = "can be switched" if DRY_RUN else "switched to bus2"
    app.PrintPlain(f"  {len(switched)} case(s) {verb}, {len(already)} already on bus2, "
                   f"{len(rerun)} need a rerun first")
    if rerun:
        app.PrintPlain("  Rerun needed (bus2 not in the results): " + ", ".join(rerun))
        if DRY_RUN:
            app.PrintPlain("  -> run with DRY_RUN = False to add bus2 to their result "
                           "objects, rerun them (frt_1, run_sim_only: true), then run "
                           "this script again.")
        else:
            app.PrintPlain("  -> bus2 added to their result objects: rerun them "
                           "(frt_1, run_sim_only: true), then run this script again.")
    for p in problems:
        app.PrintWarn(f"  {p}")
    app.PrintPlain("=" * 72)


main()
