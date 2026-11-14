# ==============================================================================
# GCC FRT: Fix POC Sign (older runs): power and current graphs
# ==============================================================================
# For FRT runs made before frt_1 v5.6: their POC page plots power and
# currents on the POC breaker's bus1 side (load oriented, export negative).
# This script does per case what you would do by hand, straight from the
# existing results (no rerun). In each graph below it
#
#   * creates a Calculated Result (IntCalcres) per curve: <variable> x -1;
#   * adds it as a curve with the original colour, line style, width and
#     legend label;
#   * hides the original curve (it stays in the table, unticked).
#
#   POC page, power graph   (bottom left,  'Curve plot(2)'):  P, Q
#   POC page, current graph (bottom right, 'Curve plot(3)'):  Ip1, Iq1, Iq2
#   GU Terminal pages, current graph (optional, off by default): the GU
#   currents are taken at the inverter, which is generator oriented, so their
#   sign is already right (active current positive, negative-sequence
#   reactive current negative during asymmetric faults).
#
# Graphs already fixed are skipped; a graph holding a hand-made Calculated
# Result is reported and left alone (delete it by hand first if the script
# should handle it). UNDO = True removes what this script added and shows the
# original curves again. DRY_RUN = True only reports.
#
# Test on one case first (CASES = ["1P-5U-Q0"], DRY_RUN = False), look at
# the graphs, then run it for all cases.
#
# Usage: point a ComPython object at this script and run it from PowerFactory.
# Dependencies: gcc_common.py and the project config in Working Scripts (one
# folder up), from the SAME version as this script.
# ==============================================================================

import os
import sys
import fnmatch

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

FLIP_POC_POWER    = True    # POC page, P and Q
FLIP_POC_CURRENTS = True    # POC page, Ip1, Iq1, Iq2
FLIP_GU_CURRENTS  = False   # GU Terminal pages, Ip1, Iq1, Iq2 (normally not needed)

# Cases. None = every case of the run; or names / report names, wildcards ok,
# e.g. ["1P-5U-Q0"] to test on one case first
CASES = None

# Run folder name in Study Cases, e.g. "FRT_2026-11-11_13-56-23 (PPC Enabled)".
# None = paths.frt_run from the project config, else the latest FRT_* run.
RUN_FOLDER = None


# ==============================================================================

# (switch, page pattern, graph, {variable: (Calculated Result name, unit)})
CURRENTS = (("m:i1P:bus1", "Ip1", "p.u."), ("m:i1Q:bus1", "Iq1", "p.u."),
            ("m:i2Q:bus1", "Iq2", "p.u."))
JOBS = (
    ("FLIP_POC_POWER", "POC", "Curve plot(2)",
     {"m:Psum:bus1": ("POC P export positive", "MW"),
      "m:Qsum:bus1": ("POC Q export positive", "Mvar")}),
    ("FLIP_POC_CURRENTS", "POC", "Curve plot(3)",
     {v: (f"POC {s} export positive", u) for v, s, u in CURRENTS}),
    ("FLIP_GU_CURRENTS", "GU Terminal*", "Curve plot(3)",
     {v: (f"GU {s} sign flipped", u) for v, s, u in CURRENTS}),
)
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


def original_curves(ds, flips):
    """{variable: index} of the curves this job flips (not Calculated Results)."""
    elms, vars_ = vec(ds, "curveTableElement"), [str(v) for v in vec(ds, "curveTableVariable")]
    return {v: i for i, (e, v) in enumerate(zip(elms, vars_))
            if v in flips and e is not None and e.GetClassName() != "IntCalcres"}


def reset_normalise(ds, indices):
    """Switch off a -1 normalisation (left by an earlier frt_7 version) on these curves."""
    norm, nval = vec(ds, "curveTableNormalise"), vec(ds, "curveTableNormValue")
    if norm and len(norm) == len(nval):
        for i in indices:
            if i < len(norm):
                norm[i], nval[i] = 0.0, 1.0
        setvec(ds, "curveTableNormalise", norm)
        setvec(ds, "curveTableNormValue", nval)


def fix_graph(ds, flips, label_name):
    """Add a Calculated Result curve (x -1) per original curve, hide the originals."""
    originals = original_curves(ds, flips)
    elms = vec(ds, "curveTableElement")
    styles = {a: vec(ds, a) for a in STYLE}
    for var, i in originals.items():
        name, unit = flips[var]
        elm = elms[i]
        calc = ds.CreateObject("IntCalcres", name)
        calc.SetAttribute("resname", name)
        calc.SetAttribute("resunit", unit)
        calc.SetAttribute("resdesc", f"{elm.loc_name} {var} x -1")
        calc.SetAttribute("formula", ["in1*-1"])
        try:
            calc.SetAttribute("element", [elm])
        except Exception:                    # operand table with fixed rows in1..in3
            calc.SetAttribute("element", [elm, None, None])
        calc.SetAttribute("inpvar", [var])
        ds.AddCurve(calc, name)
    names = {n for n, _ in flips.values()}
    n = len(vec(ds, "curveTableVariable"))
    new_idx = {str(v): k for k, v in enumerate(vec(ds, "curveTableVariable")) if str(v) in names}
    for attr in STYLE:
        cur = (vec(ds, attr) + [None] * n)[:n]
        for var, i in originals.items():
            k = new_idx[flips[var][0]]
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
    visible = (vec(ds, "curveTableVisible") + [1.0] * n)[:n]
    for var, i in originals.items():
        visible[i] = 0.0
        visible[new_idx[flips[var][0]]] = 1.0
    setvec(ds, "curveTableVisible", visible)
    reset_normalise(ds, originals.values())
    return "flipped " + ", ".join(v.split(":")[1] for v in originals)


def undo_graph(ds, flips):
    """Remove this script's curves and Calculated Results, show the originals again."""
    names = {n for n, _ in flips.values()}
    vars_ = [str(v) for v in vec(ds, "curveTableVariable")]
    ours = [k for k, v in enumerate(vars_) if v in names]
    for k in sorted(ours, reverse=True):
        ds.RemoveCurve(k)
    for calc in ds.GetIntCalcres() or []:
        if calc.loc_name in names:
            calc.Delete()
    originals = original_curves(ds, flips)
    n = len(vec(ds, "curveTableVariable"))
    visible = (vec(ds, "curveTableVisible") + [1.0] * n)[:n]
    for i in originals.values():
        visible[i] = 1.0
    setvec(ds, "curveTableVisible", visible)
    reset_normalise(ds, originals.values())
    return f"removed {len(ours)} curve(s), originals shown"


def handle_graph(ds, flips, label_name):
    """One graph: returns (changed?, message)."""
    names = {n for n, _ in flips.values()}
    calcs = ds.GetIntCalcres() or []
    ours = [c for c in calcs if c.loc_name in names]
    foreign = [c for c in calcs if c.loc_name not in names]
    if foreign and not UNDO:
        return False, f"hand-made Calculated Result '{foreign[0].loc_name}': left as it is"
    if UNDO:
        if not ours:
            return False, "not flipped"
        return True, ("would be undone" if DRY_RUN else undo_graph(ds, flips))
    if ours:
        return False, "already flipped"
    if not original_curves(ds, flips):
        return False, "no bus1 curves to flip"
    return True, ("would be flipped" if DRY_RUN else fix_graph(ds, flips, label_name))


def main():
    app = powerfactory.GetApplication()
    app.ClearOutputWindow()
    mode = "DRY RUN: nothing changed" if DRY_RUN else ("UNDO" if UNDO else "WRITING")
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  GCC FRT: Fix POC Sign   ({mode})")
    app.PrintPlain("=" * 72)

    cfg = gcc.load_yaml(gcc.resolve_config_path(__file__))
    labels = gcc.plot_label_names(cfg)
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
    jobs = [j for j in JOBS if globals()[j[0]]]
    app.PrintPlain(f"  Run folder : {folder.loc_name}   ({len(cases)} case(s))")
    app.PrintPlain("  Graphs     : " + "; ".join(f"{p} {g}" for _, p, g, _ in jobs) + "\n")

    n_changed, problems = 0, []
    app.EchoOff()
    try:
        for case in cases:
            case.Activate()
            board = app.GetGraphicsBoard()
            pages = board.GetContents("*.GrpPage") if board else []
            parts = []
            for _, page_pat, graph, flips in jobs:
                for page in [p for p in pages if fnmatch.fnmatch(p.loc_name, page_pat)]:
                    plots = page.GetContents(f"{graph}.PltLinebarplot")
                    if not plots:
                        problems.append(f"{case.loc_name}: no '{graph}' on '{page.loc_name}'")
                        continue
                    label_name = (labels["poc_breaker"] if page_pat == "POC" else
                                  labels["inverter2"] if page.loc_name.endswith("2") else
                                  labels["inverter1"])
                    try:
                        changed, msg = handle_graph(plots[0].GetDataSeries(), flips, label_name)
                    except Exception as exc:
                        problems.append(f"{case.loc_name} / {page.loc_name} {graph}: {exc}")
                        continue
                    n_changed += changed
                    kind = "power" if graph == "Curve plot(2)" else "currents"
                    parts.append(f"{page.loc_name} {kind}: {msg}")
            app.PrintPlain(f"  {case.loc_name:22s} " + " | ".join(parts))
    finally:
        active = app.GetActiveStudyCase()
        if active is not None:
            active.Deactivate()
        app.EchoOn()

    app.PrintPlain("")
    app.PrintPlain("=" * 72)
    verb = "would change" if DRY_RUN else "changed"
    app.PrintPlain(f"  {n_changed} graph(s) {verb} in {len(cases)} case(s)")
    for p in problems:
        app.PrintWarn(f"  {p}")
    app.PrintPlain("=" * 72)


main()
