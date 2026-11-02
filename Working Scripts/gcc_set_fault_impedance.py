# ==============================================================================
# GCC FRT — Set Fault Impedances
# ==============================================================================
# Overwrites the fault impedance (R_f, X_f) of the fault events in every study
# case of the latest FRT run folder, using the values in the table below.
#
# gcc_frt_script.py pre-fills each case with an ESTIMATED impedance. The
# retained voltage the plant actually sees is usually different, so tune the
# values here and re-run this script — no need to rebuild the study cases.
#
# Every case with the same retained voltage gets the same impedance, whatever
# its fault type or Q condition:
#   "FRT 3PH 0.05 Q0", "FRT 1PH 0.05 Qmax", ...  -> FAULT_IMPEDANCE[0.05]
#
# Only the fault-APPLY events are changed; the clearing events carry no
# impedance and are left alone. Retained voltage levels set to None are left
# unchanged.
#
# Usage:
#   1. Fill in FAULT_IMPEDANCE below
#   2. Set DRY_RUN = True to only list the current values, False to write
#   3. Point a ComPython object at this script and run it from PowerFactory
#   4. Re-run the FRT simulations (setup_only: false, run_sim_only: true)
#
# Dependencies: gcc_common.py in the same folder as this script.
# ==============================================================================

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.modules.pop("gcc_common", None)      # always load gcc_common fresh
import gcc_common as gcc

import powerfactory


# ------------------------------------------------------------------------------
# FAULT IMPEDANCE PER RETAINED VOLTAGE
#   Uret [pu] :  (R_f [Ohm], X_f [Ohm])        None = leave unchanged
# ------------------------------------------------------------------------------
FAULT_IMPEDANCE = {
    0.05: None,
    0.20: None,
    0.40: None,
    0.60: None,
    0.85: None,
}
# Example:
#   0.85: (2.10, 21.0),

# True  = only list the current impedances, change nothing
# False = write the values above
DRY_RUN = True

# Run folder to update. None = the latest FRT_* folder in Study Cases.
RUN_TAG = None

I_SHC_CLEAR = 4          # EvtShc i_shc value of a "clear" event


# ==============================================================================

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


def case_uret(case_name):
    """Retained voltage from a case name: 'FRT 3PH 0.05 Q0' -> 0.05."""
    parts = case_name.split()
    try:
        return float(parts[2])
    except (IndexError, ValueError):
        return None


def impedance_for(uret):
    """(R, X) from FAULT_IMPEDANCE for this Uret, or None if not set."""
    for level, rx in FAULT_IMPEDANCE.items():
        if abs(level - uret) < 1e-6:
            return rx
    return None


def fault_events(case):
    """The fault-apply EvtShc events of a study case (clear events skipped)."""
    return [e for e in case.GetContents("*.EvtShc", 1)
            if int(e.i_shc) != I_SHC_CLEAR]


def _run(app):
    run_folder = find_run_folder(app)
    cases = sorted(run_folder.GetContents("*.IntCase"),
                   key=lambda c: (case_uret(c.loc_name) or 0, c.loc_name))

    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  GCC FRT — Set Fault Impedances  "
                   f"({'DRY RUN — nothing written' if DRY_RUN else 'WRITING'})")
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  Run folder : {run_folder.loc_name}   ({len(cases)} cases)\n")
    app.PrintPlain(f"  {'Case':24s} {'R now':>9s} {'X now':>9s}   "
                   f"{'R new':>9s} {'X new':>9s}")
    app.PrintPlain("  " + "-" * 66)

    n_changed, skipped = 0, []
    for case in cases:
        name = case.loc_name
        uret = case_uret(name)
        if uret is None:
            skipped.append(f"{name}  (name does not contain a Uret)")
            continue

        events = fault_events(case)
        if not events:
            skipped.append(f"{name}  (no fault event found)")
            continue

        rx = impedance_for(uret)
        for evt in events:
            new = f"{rx[0]:9.4f} {rx[1]:9.4f}" if rx else f"{'—':>9s} {'—':>9s}"
            app.PrintPlain(f"  {name:24s} {evt.R_f:9.4f} {evt.X_f:9.4f}   {new}")
            if rx and not DRY_RUN:
                evt.R_f = float(rx[0])
                evt.X_f = float(rx[1])
                n_changed += 1

    app.PrintPlain("  " + "-" * 66)
    if DRY_RUN:
        app.PrintPlain("  DRY RUN — set DRY_RUN = False to write the new values.")
    else:
        app.PrintPlain(f"  {n_changed} fault event(s) updated.")

    unset = [f"{u:g}" for u, rx in FAULT_IMPEDANCE.items() if rx is None]
    if unset:
        app.PrintPlain(f"  Left unchanged (None in table): Uret {', '.join(unset)} pu")
    for line in skipped:
        app.PrintWarn(f"  Skipped: {line}")

    if not DRY_RUN and n_changed:
        app.PrintPlain("\n  Next: re-run the FRT simulations "
                       "(setup_only: false, run_sim_only: true).")
    app.PrintPlain("=" * 72)


def main():
    app = powerfactory.GetApplication()
    with gcc.pf_session(app):
        try:
            _run(app)
        except Exception as exc:
            app.PrintError(f"Set fault impedances aborted: {exc}")
            raise


main()
