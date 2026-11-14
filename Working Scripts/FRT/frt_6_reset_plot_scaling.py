# ==============================================================================
# GCC FRT: Reset Plot Scaling
# ==============================================================================
# Puts the y-axes of the FRT plot pages back to fixed ranges, in every study
# case of the latest FRT run (or the cases / run set below):
#
#   voltage graphs  (phase voltages, sequence voltages)   Y_RANGE_VOLTAGE
#   current graphs  (active / reactive currents)          Y_RANGE_CURRENT
#
# on the POC page and every GU Terminal page. The active / reactive power
# graph and the time axis are left as they are. Use it after zooming or
# rescaling graphs by hand, before exporting the plots (frt_4_export_plots.py).
# Nothing else in the cases is changed.
#
# Usage: point a ComPython object at this script and run it from PowerFactory.
# Dependencies: gcc_common.py and the project config in Working Scripts (one
# folder up).
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
Y_RANGE_VOLTAGE = (0.0, 1.4)     # [pu] phase and sequence voltage graphs
Y_RANGE_CURRENT = (-1.0, 1.4)    # [pu] active / reactive current graphs

# Cases to reset. None = every case of the run. Otherwise PowerFactory or
# report names, wildcards allowed, e.g. ["1P-60U-Qmax", "FRT 2PH * Qmax"]
CASES = None

# Run folder in Study Cases. None = paths.frt_run from the project config,
# else the latest FRT_* run.
RUN_FOLDER = None


# ==============================================================================

# Graphs on every FRT page, as created by frt_1_create_and_run_cases.py:
#   'Curve plot' phase voltages, 'Curve plot(1)' sequence voltages,
#   'Curve plot(2)' P / Q (not touched), 'Curve plot(3)' currents
PAGE_PATTERNS = ("POC", "GU Terminal*")
GRAPHS = (("Curve plot", "voltage"), ("Curve plot(1)", "voltage"),
          ("Curve plot(3)", "current"))


def main():
    app = powerfactory.GetApplication()
    app.ClearOutputWindow()
    app.PrintPlain("=" * 72)
    app.PrintPlain("  GCC FRT: Reset Plot Scaling")
    app.PrintPlain("=" * 72)

    cfg = gcc.load_yaml(gcc.resolve_config_path(__file__))
    study = gcc.get_folder(app, "study")
    run = RUN_FOLDER or gcc.run_name_setting(cfg, "FRT")
    if run:
        folder = gcc.find_run_folder_any(app, run)
    else:
        folder = gcc.find_latest_run_folder(study, "FRT")
        if folder is None:
            app.PrintError("No FRT_* run folder in Study Cases.")
            return
    cases = sorted(folder.GetContents("*.IntCase", 1), key=lambda c: c.loc_name)
    if CASES:
        cases, unmatched = gcc.select_cases(cases, CASES)
        for pat in unmatched:
            app.PrintWarn(f"  No case matches '{pat}'")
    ranges = {"voltage": Y_RANGE_VOLTAGE, "current": Y_RANGE_CURRENT}
    app.PrintPlain(f"  Run folder : {folder.loc_name}   ({len(cases)} case(s))")
    app.PrintPlain(f"  Voltages   : {Y_RANGE_VOLTAGE[0]:g} to {Y_RANGE_VOLTAGE[1]:g} pu")
    app.PrintPlain(f"  Currents   : {Y_RANGE_CURRENT[0]:g} to {Y_RANGE_CURRENT[1]:g} pu\n")

    n_graphs, problems = 0, []
    app.EchoOff()
    try:
        for case in cases:
            case.Activate()
            board = app.GetGraphicsBoard()
            pages = []
            if board is not None:
                for p in board.GetContents("*.GrpPage") or []:
                    if any(fnmatch.fnmatch(p.loc_name, pat) for pat in PAGE_PATTERNS):
                        pages.append(p)
            if not pages:
                problems.append(f"{case.loc_name}: no POC / GU Terminal pages")
                continue
            done = 0
            for page in pages:
                for graph, kind in GRAPHS:
                    plots = page.GetContents(f"{graph}.PltLinebarplot")
                    if not plots:
                        problems.append(f"{case.loc_name} / {page.loc_name}: no '{graph}'")
                        continue
                    gcc.set_yrange(plots[0], *ranges[kind], app)
                    done += 1
            n_graphs += done
            app.PrintPlain(f"  {case.loc_name:28s} -> {done} graph(s) on "
                           + ", ".join(p.loc_name for p in pages))
    finally:
        active = app.GetActiveStudyCase()
        if active is not None:
            active.Deactivate()
        app.EchoOn()

    app.PrintPlain("")
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  {n_graphs} graph(s) reset in {len(cases)} case(s)")
    for p in problems:
        app.PrintWarn(f"  {p}")
    app.PrintPlain("=" * 72)


main()
