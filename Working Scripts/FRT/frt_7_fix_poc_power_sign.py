# ==============================================================================
# GCC FRT: Fix POC Power Sign (older runs)
# ==============================================================================
# For FRT runs made before frt_1 v5.6: their POC page plots active and
# reactive power on the POC breaker's bus1 side (load oriented, export
# negative). This script does in ONE graph per case (POC page, bottom left,
# 'Curve plot(2)') what you would do by hand, straight from the existing
# results (no rerun):
#
#   * creates two Calculated Results (IntCalcres) in the graph's data series:
#       "POC P export positive" = POC breaker m:Psum:bus1 x -1   [MW]
#       "POC Q export positive" = POC breaker m:Qsum:bus1 x -1   [Mvar]
#   * adds them as curves with the colour, line style, width and legend
#     label of the original curves;
#   * hides the original bus1 curves (they stay in the table, unticked).
#
# Cases already done are skipped. A hand-made Calculated Result in the graph
# is reported and that case left alone (delete it by hand first if the script
# should handle the case). UNDO = True removes what this script added and
# shows the original curves again. DRY_RUN = True only reports.
#
# Test on one case first (CASES = ["1P-5U-Q0"], DRY_RUN = False), look at
# the graph, then run it for all cases.
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
UNDO    = False    # True = remove what this script added, show the originals

# Cases. None = every case of the run; or names / report names, wildcards ok,
# e.g. ["1P-5U-Q0"] to test on one case first
CASES = None

# Run folder name in Study Cases, e.g. "FRT_2026-11-11_13-56-23 (PPC Enabled)".
# None = paths.frt_run from the project config, else the latest FRT_* run.
RUN_FOLDER = None


# ==============================================================================

PAGE, GRAPH = "POC", "Curve plot(2)"
# original variable -> (Calculated Result name, unit)
CALCS = {
    "m:Psum:bus1": ("POC P export positive", "MW"),
    "m:Qsum:bus1": ("POC Q export positive", "Mvar"),
}
OUR_NAMES = {name for name, _ in CALCS.values()}
STYLE = ("curveTableColor", "curveTableLineStyle", "curveTableLineWidth", "curveTableLabel")


def vec(ds, attr):
    try:
        return list(ds.GetAttribute(attr) or [])
    except Exception:
        return []


def setvec(ds, attr, values):
    ds.SetAttribute(attr, list(values))


def find_run_folder(study, name):
    found = [f for f in study.GetContents("*.IntFolder", 1) if f.loc_name == name]
    if not found:
        raise RuntimeError(f"Run folder '{name}' not found under Study Cases.")
    return found[0]


def is_poc(elm, brk):
    return elm is not None and getattr(elm, "loc_name", None) == brk.loc_name \
        and elm.GetClassName() == "ElmCoup"


def original_curves(ds, brk):
    """{variable: index} of the POC breaker's bus1 P / Q curves."""
    elms, vars_ = vec(ds, "curveTableElement"), [str(v) for v in vec(ds, "curveTableVariable")]
    return {v: i for i, (e, v) in enumerate(zip(elms, vars_)) if v in CALCS and is_poc(e, brk)}


def reset_normalise(ds, indices):
    """Switch off a -1 normalisation (left by an earlier frt_7 version) on these curves."""
    norm, nval = vec(ds, "curveTableNormalise"), vec(ds, "curveTableNormValue")
    if norm and len(norm) == len(nval):
        for i in indices:
            if i < len(norm):
                norm[i], nval[i] = 0.0, 1.0
        setvec(ds, "curveTableNormalise", norm)
        setvec(ds, "curveTableNormValue", nval)


def fix_case(ds, brk, label_name):
    """Add the two Calculated Results as curves, hide the originals. Returns a message."""
    originals = original_curves(ds, brk)
    if not originals:
        return "no POC breaker bus1 P / Q curves: nothing to do"
    styles = {a: vec(ds, a) for a in STYLE}
    for var, i in originals.items():
        name, unit = CALCS[var]
        calc = ds.CreateObject("IntCalcres", name)
        calc.SetAttribute("resname", name)
        calc.SetAttribute("resunit", unit)
        calc.SetAttribute("resdesc", f"{brk.loc_name} {var} x -1 (export positive)")
        calc.SetAttribute("formula", ["in1*-1"])
        try:
            calc.SetAttribute("element", [brk])
        except Exception:                    # operand table with fixed rows in1..in3
            calc.SetAttribute("element", [brk, None, None])
        calc.SetAttribute("inpvar", [var])
        ds.AddCurve(calc, name)
    n = len(vec(ds, "curveTableVariable"))
    new_idx = {str(v): k for k, v in enumerate(vec(ds, "curveTableVariable"))
               if str(v) in OUR_NAMES}
    visible = (vec(ds, "curveTableVisible") + [1.0] * n)[:n]
    for attr in STYLE:
        cur = (vec(ds, attr) + [None] * n)[:n]
        for var, i in originals.items():
            k = new_idx[CALCS[var][0]]
            src = styles[attr][i] if i < len(styles[attr]) else None
            if attr == "curveTableLabel" and not src:
                src = gcc.curve_label(label_name, var)
            if src is not None:
                cur[k] = src
        if all(c is not None for c in cur):
            try:
                setvec(ds, attr, cur)
            except Exception:
                pass
    for var, i in originals.items():
        visible[i] = 0.0
        visible[new_idx[CALCS[var][0]]] = 1.0
    setvec(ds, "curveTableVisible", visible)
    reset_normalise(ds, originals.values())
    return "added " + ", ".join(CALCS[v][0] for v in originals) + "; originals hidden"


def undo_case(ds, brk):
    """Remove this script's curves and Calculated Results, show the originals again."""
    vars_ = [str(v) for v in vec(ds, "curveTableVariable")]
    ours = [k for k, v in enumerate(vars_) if v in OUR_NAMES]
    for k in sorted(ours, reverse=True):
        ds.RemoveCurve(k)
    for calc in ds.GetIntCalcres() or []:
        if calc.loc_name in OUR_NAMES:
            calc.Delete()
    originals = original_curves(ds, brk)
    n = len(vec(ds, "curveTableVariable"))
    visible = (vec(ds, "curveTableVisible") + [1.0] * n)[:n]
    for i in originals.values():
        visible[i] = 1.0
    setvec(ds, "curveTableVisible", visible)
    reset_normalise(ds, originals.values())
    return f"removed {len(ours)} curve(s); originals shown"


def main():
    app = powerfactory.GetApplication()
    app.ClearOutputWindow()
    mode = "DRY RUN: nothing changed" if DRY_RUN else ("UNDO" if UNDO else "WRITING")
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  GCC FRT: Fix POC Power Sign   ({mode})")
    app.PrintPlain("=" * 72)

    cfg = gcc.load_yaml(gcc.resolve_config_path(__file__))
    brk = gcc.find_element(app, "ElmCoup", (cfg.get("elements") or {}).get("poc_breaker"))
    label_name = gcc.plot_label_names(cfg)["poc_breaker"]
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
    app.PrintPlain(f"  Run folder  : {folder.loc_name}   ({len(cases)} case(s))")
    app.PrintPlain(f"  POC breaker : {brk.loc_name}\n")

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
            calcs = ds.GetIntCalcres() or []
            ours = [c for c in calcs if c.loc_name in OUR_NAMES]
            foreign = [c for c in calcs if c.loc_name not in OUR_NAMES]
            if foreign and not UNDO:
                skipped.append(name)
                app.PrintPlain(f"  {name:24s} hand-made Calculated Result "
                               f"'{foreign[0].loc_name}' found: left as it is")
                continue
            if not UNDO and not ours and not original_curves(ds, brk):
                skipped.append(name)
                app.PrintPlain(f"  {name:24s} no POC breaker bus1 P / Q curves: nothing to do")
                continue
            if bool(ours) != UNDO:
                skipped.append(name)
                app.PrintPlain(f"  {name:24s} {'not fixed' if UNDO else 'already fixed'}: "
                               f"nothing to do")
                continue
            if DRY_RUN:
                changed.append(name)
                app.PrintPlain(f"  {name:24s} would be {'undone' if UNDO else 'fixed'}")
                continue
            try:
                msg = undo_case(ds, brk) if UNDO else fix_case(ds, brk, label_name)
            except Exception as exc:
                problems.append(f"{name}: {exc}")
                continue
            changed.append(name)
            app.PrintPlain(f"  {name:24s} {msg}")
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
