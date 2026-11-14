# ==============================================================================
# GCC FRT: Fix POC Power Sign (older runs)
# ==============================================================================
# For FRT runs made before frt_1 v5.6: their POC page plots active and
# reactive power on the POC breaker's bus1 side (load oriented, export
# negative). This script flips the sign of those two curves in ONE graph per
# case (POC page, bottom left, 'Curve plot(2)') straight from the existing
# results, without a rerun: each curve is normalised by -1 (the curve table's
# "Normalise" option, value / -1 = value x -1). Colours and line styles stay;
# empty legend labels are filled in ("POC Total Active Power (MW)", ...).
#
# Skipped per case (and listed): curves already normalised by -1, curves
# already on bus2, and graphs holding a hand-made Calculated Result.
# UNDO = True switches the normalisation off again.
#
# Test on one case first (CASES = ["1P-5U-Qmin"], DRY_RUN = False), look at
# the graph, then run it for all cases. DRY_RUN = True only reports.
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
UNDO    = False    # True = switch the -1 normalisation off again

# Cases. None = every case of the run; or names / report names, wildcards ok,
# e.g. ["1P-5U-Qmin"] to test on one case first
CASES = None

# Run folder name in Study Cases, e.g. "FRT_2026-11-11_13-56-23 (PPC Enabled)".
# None = paths.frt_run from the project config, else the latest FRT_* run.
RUN_FOLDER = None


# ==============================================================================

PAGE, GRAPH = "POC", "Curve plot(2)"
FLIP_VARS = ("m:Psum:bus1", "m:Qsum:bus1")


def _vec(ds, attr, n, default):
    """A curve-table vector as a list of length n."""
    try:
        val = list(ds.GetAttribute(attr) or [])
    except Exception:
        val = []
    return (val + [default] * n)[:n]


def find_run_folder(app, study, name):
    """The IntFolder called `name` anywhere under Study Cases."""
    found = [f for f in study.GetContents("*.IntFolder", 1) if f.loc_name == name]
    if not found:
        raise RuntimeError(f"Run folder '{name}' not found under Study Cases.")
    return found[0]


def main():
    app = powerfactory.GetApplication()
    app.ClearOutputWindow()
    mode = "DRY RUN: nothing changed" if DRY_RUN else ("UNDO" if UNDO else "WRITING")
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  GCC FRT: Fix POC Power Sign   ({mode})")
    app.PrintPlain("=" * 72)

    cfg = gcc.load_yaml(gcc.resolve_config_path(__file__))
    label_name = gcc.plot_label_names(cfg)["poc_breaker"]
    study = gcc.get_folder(app, "study")
    run = RUN_FOLDER or gcc.run_name_setting(cfg, "FRT")
    folder = find_run_folder(app, study, run) if run else gcc.find_latest_run_folder(study, "FRT")
    if folder is None:
        app.PrintError("No FRT_* run folder in Study Cases.")
        return
    cases = sorted(folder.GetContents("*.IntCase", 1), key=lambda c: c.loc_name)
    if CASES:
        cases, unmatched = gcc.select_cases(cases, CASES)
        for pat in unmatched:
            app.PrintWarn(f"  No case matches '{pat}'")
    app.PrintPlain(f"  Run folder : {folder.loc_name}   ({len(cases)} case(s))\n")

    changed, skipped, problems = [], [], []
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
            if ds.GetIntCalcres():
                skipped.append(name)
                app.PrintPlain(f"  {name:24s} hand-made Calculated Result found: left as it is")
                continue
            try:
                vars_ = [str(v) for v in (ds.GetAttribute("curveTableVariable") or [])]
            except Exception:
                vars_ = []
            n = len(vars_)
            if n == 0:
                problems.append(f"{name}: no curves in '{GRAPH}'")
                continue
            norm = _vec(ds, "curveTableNormalise", n, 0.0)
            value = _vec(ds, "curveTableNormValue", n, 1.0)
            labels = [str(x) for x in _vec(ds, "curveTableLabel", n, "")]

            todo = []
            for i, var in enumerate(vars_):
                if var not in FLIP_VARS:
                    continue
                flipped = float(norm[i]) == 1.0 and float(value[i]) == -1.0
                if flipped != UNDO:
                    continue                     # already in the wanted state
                todo.append(i)
            if not todo:
                skipped.append(name)
                app.PrintPlain(f"  {name:24s} {vars_}: nothing to do")
                continue
            app.PrintPlain(f"  {name:24s} {'un' if UNDO else ''}flip "
                           + ", ".join(vars_[i] for i in todo))
            if DRY_RUN:
                changed.append(name)
                continue
            for i in todo:
                norm[i], value[i] = (0.0, 1.0) if UNDO else (1.0, -1.0)
                if not labels[i]:
                    labels[i] = gcc.curve_label(label_name, vars_[i])
            try:
                ds.SetAttribute("curveTableNormalise", norm)
                ds.SetAttribute("curveTableNormValue", value)
                ds.SetAttribute("curveTableLabel", labels)
            except Exception as exc:
                problems.append(f"{name}: could not write the curve table ({exc})")
                continue
            changed.append(name)
    finally:
        active = app.GetActiveStudyCase()
        if active is not None:
            active.Deactivate()
        app.EchoOn()

    app.PrintPlain("")
    app.PrintPlain("=" * 72)
    verb = "would change" if DRY_RUN else "changed"
    app.PrintPlain(f"  {len(changed)} case(s) {verb}, {len(skipped)} left as they are")
    for p in problems:
        app.PrintWarn(f"  {p}")
    app.PrintPlain("=" * 72)


main()
