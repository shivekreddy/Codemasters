# ==============================================================================
# GCC FRT — Run Selected Cases Sequentially
# ==============================================================================
# Runs a selection of the FRT study cases in the latest FRT_* run folder, one
# after the other, from Python. Use it instead of Task Automation.
#
# For every case in CASES (in that order):
#   1. activate the study case
#   2. calculate initial conditions          -> fails: case skipped
#   3. run the RMS simulation                -> fails / diverges /
#                                               takes longer than
#                                               MAX_RUNTIME_S: case skipped
#   and move on to the next case. The output window ends with a summary of
#   the completed and the skipped cases (with the reason for each).
#
# THE TIME LIMIT
#   PowerFactory cannot interrupt a running simulation from Python, so the
#   simulation is run in slices of SLICE_S seconds of simulation time and the
#   elapsed time is checked after every slice. A case is therefore stopped at
#   the end of the slice in which it passes MAX_RUNTIME_S. The case's own stop
#   time is restored afterwards — nothing else in the case is changed.
#
# Usage:
#   1. Run gcc_frt_script.py first (it creates the FRT_* run folder)
#   2. Adjust CASES below if needed (None = every case in the run folder)
#   3. Point a ComPython object at this script and run it from PowerFactory
#   4. Export the results with gcc_export_frt.py
#
# Dependencies: gcc_common.py in the same folder as this script.
# ==============================================================================

import sys
import os
import time as clock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.modules.pop("gcc_common", None)      # always load gcc_common fresh
import gcc_common as gcc

import powerfactory


# ------------------------------------------------------------------------------
# CASES TO RUN — study case names, in run order. None = every case.
# ------------------------------------------------------------------------------
CASES = [
    "FRT 3PH 0.05 Q0",     # 1  most severe balanced fault: ride-through, current
                           #    limit, P recovery; note residual U at inverters
    "FRT 3PH 0.05 Qmin",   # 2  largest Iq change (absorbing -> max injection);
                           #    slow rise / overshoot; reduced P
    "FRT 3PH 0.05 Qmax",   # 3  reduced P at max Q: least current-limit headroom
    "FRT 3PH 0.2 Q0",      # 4  first depth with all FFCI criteria; 900 ms fault
    "FRT 3PH 0.6 Q0",      # 5  long fault: P recovery, PPC wind-up, tap changers
    "FRT 3PH 0.85 Q0",     # 6  SRT mode, no current limit: cleanest k check
    "FRT 2PH 0.05 Q0",     # 7  worst unbalanced case: negative sequence,
                           #    phase current limit
    "FRT 2PH 0.2 Qmin",    # 8  case 7 behaviour + large Iq change
    "FRT 1PH 0.05 Q0",     # 9  zero-sequence assumption; inverter mode
]

# Skip a case if its simulation runs longer than this [s, wall-clock time]
MAX_RUNTIME_S = 600

# Simulation time per slice [s] — how often the time limit is checked.
# None = run each simulation in one go (no time limit applied).
SLICE_S = 1.0

# Run folder. None = the latest FRT_* folder in Study Cases.
RUN_TAG = None


# ==============================================================================

class CaseSkipped(Exception):
    """Raised to skip a case; the message is the reason shown in the summary."""


def find_run_folder(app):
    """The run folder named by RUN_TAG, or the latest FRT_* folder."""
    study = gcc.get_folder(app, "study")
    if RUN_TAG:
        found = study.GetContents(f"{RUN_TAG}.IntFolder")
        if not found:
            raise RuntimeError(f"Run folder '{RUN_TAG}' not found in Study Cases.")
        return found[0]
    folder = gcc.find_latest_run_folder(study, "FRT")
    if folder is None:
        raise RuntimeError("No FRT_* run folder found in Study Cases. "
                           "Run gcc_frt_script.py first.")
    return folder


def run_case(app, case):
    """
    Initial conditions + simulation for one study case, within MAX_RUNTIME_S.
    Raises CaseSkipped with the reason if it does not complete.
    Returns the simulation runtime [s].
    """
    case.Activate()
    comInc = app.GetFromStudyCase("ComInc")
    comSim = app.GetFromStudyCase("ComSim")

    if comInc.Execute():
        raise CaseSkipped("initial conditions did not converge")

    t_stop  = float(comSim.tstop)
    t_sim   = float(comInc.tstart)
    started = clock.perf_counter()
    if SLICE_S is None:                  # one go, no time limit
        if comSim.Execute():
            raise CaseSkipped("simulation did not converge")
        t_sim = t_stop
    try:
        while t_sim < t_stop - 1e-9:
            t_sim = min(t_sim + SLICE_S, t_stop)
            comSim.tstop = t_sim
            if comSim.Execute():
                raise CaseSkipped(f"simulation did not converge "
                                  f"(stopped near t = {t_sim:g} s)")
            elapsed = clock.perf_counter() - started
            if elapsed > MAX_RUNTIME_S and t_sim < t_stop - 1e-9:
                raise CaseSkipped(f"exceeded {MAX_RUNTIME_S} s runtime "
                                  f"(reached t = {t_sim:g} of {t_stop:g} s)")
    finally:
        comSim.tstop = t_stop            # leave the case as it was

    warn_a = comSim.GetTotalWarnA()
    if warn_a > 0:
        raise CaseSkipped(f"simulation diverged (b:warnA = {warn_a})")
    return clock.perf_counter() - started


def _run(app):
    run_folder = find_run_folder(app)
    in_folder  = {c.loc_name: c for c in run_folder.GetContents("*.IntCase")}
    names      = CASES if CASES is not None else sorted(in_folder)

    app.PrintPlain("=" * 72)
    app.PrintPlain("  GCC FRT — Run Selected Cases Sequentially")
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  Run folder : {run_folder.loc_name}")
    app.PrintPlain(f"  Cases      : {len(names)}   " + (
        f"(time limit {MAX_RUNTIME_S} s per case, checked every {SLICE_S:g} s "
        f"of simulation)" if SLICE_S else "(no time limit: SLICE_S = None)"))

    completed, skipped = [], []           # [(name, runtime)], [(name, reason)]
    t_start = clock.perf_counter()

    for i, name in enumerate(names, start=1):
        app.PrintPlain(f"\n  [{i}/{len(names)}] {name}")
        case = in_folder.get(name)
        if case is None:
            skipped.append((name, "study case not found in the run folder"))
            app.PrintWarn("    SKIPPED — study case not found in the run folder")
            continue
        try:
            runtime = run_case(app, case)
            completed.append((name, runtime))
            app.PrintPlain(f"    done in {runtime:.0f} s")
        except CaseSkipped as reason:
            skipped.append((name, str(reason)))
            app.PrintWarn(f"    SKIPPED — {reason}")
        except Exception as exc:          # any other PF error: skip, keep going
            skipped.append((name, f"error: {exc}"))
            app.PrintWarn(f"    SKIPPED — error: {exc}")

    total = clock.perf_counter() - t_start
    app.PrintPlain("")
    app.PrintPlain("=" * 72)
    app.PrintPlain("  SUMMARY")
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  Run folder : {run_folder.loc_name}")
    app.PrintPlain(f"  Completed  : {len(completed)} / {len(names)}   "
                   f"(total {total / 60:.1f} min)")
    for name, runtime in completed:
        app.PrintPlain(f"    ok  {name:24s} {runtime:6.0f} s")
    if skipped:
        app.PrintPlain(f"\n  SKIPPED    : {len(skipped)}")
        for name, reason in skipped:
            app.PrintPlain(f"    x   {name:24s} {reason}")
    app.PrintPlain("\n  Next: export the results with gcc_export_frt.py")
    app.PrintPlain("=" * 72)


def main():
    app = powerfactory.GetApplication()
    with gcc.pf_session(app):
        try:
            _run(app)
        except Exception as exc:
            app.PrintError(f"Sequential FRT run aborted: {exc}")
            raise


main()
