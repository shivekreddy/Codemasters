# ==============================================================================
# GCC FRT Automation Script
# ==============================================================================
# Reads ONE project config file (project_config.yaml) and ONE grid code file,
# then automatically:
#   1. Derives all FRT study cases from the grid code (no manual case lists)
#   2. Creates operational scenarios and copies GCC_BaseCase for each case
#   3. Writes setpoints, fault events, RMS settings, and plot pages
#   4. Runs all simulations (if setup_only = false)
#   5. Exports results to a timestamped CSV + JSON run manifest
#
# Usage:
#   1. Edit project_config.yaml for your project (plant data, element names,
#      grid code file path, output folder)
#   2. Set CONFIG_FILE below — or leave it as None to use the
#      project_config.yaml sitting NEXT TO this script
#   3. Point a ComPython object at this script and run from PowerFactory
#
# Dependencies:
#   PyYAML         — "<PF Python>\python.exe" -m pip install pyyaml
#   gcc_common.py  — must sit in the SAME FOLDER as this script
#
# Author  : GCC Automation Team
# Version : 4.2
#
# CHANGELOG v4.1 -> v4.2
# ----------------------
#   * RECORD_VARS now imported from gcc_common (RECORD_VARS_FRT) — single
#     authoritative definition shared with the exporter.
#   * REMOVED: in-script CSV export. Results export is now exclusively the
#     job of gcc_export_results.py (one CSV per case + export_manifest.json,
#     the evaluation tool's contract). Run it after the batch completes.
#     Requires gcc_common v1.1+.
#   * CONFIG_FILE may now point at a FOLDER — the script looks for
#     project_config.yaml / project_config_update.yaml inside it.
#
# CHANGELOG v4.0 -> v4.1
# ----------------------
#   * NEW: Task Automation (ComTasks) population and execution, enabling
#     PARALLEL processing of the FRT case batch.
#       - populate_task_automation() uses ComTasks.AppendStudyCase() and
#         ComTasks.AppendCommand(), NOT CreateObject(). The old CreateObject
#         approach rebuilt the ComTasks child table on every call, so only
#         the last case survived — that is why the 2-click manual finish was
#         previously needed. The Append* API writes the table directly and
#         is verified per call via its 0/1 return code.
#       - ComInc ("Calculation of Initial Conditions") is appended BEFORE
#         ComSim ("Run Simulation") for each case; reversing them makes Task
#         Automation attempt the simulation before initialisation.
#       - The ComTasks object lives at the Study Cases root (as PF creates
#         it). RemoveStudyCases() clears it before each population, so a
#         re-run never leaves stale rows behind.
#   * NEW simulation.execution_mode: 'sequential' | 'task_automation'
#       - sequential      : v4.0 behaviour — each case run from Python with
#                           three-layer convergence detection.
#       - task_automation : cases are populated into ComTasks and executed
#                           there (parallel-capable).
#     Applies to BOTH the setup+run and run_sim_only paths, so behaviour is
#     identical whether cases are newly created or re-executed.
#     This key is FRT-only; gcc_freq_script.py always runs sequentially
#     (6 long cases do not parallelise usefully and need the per-case
#     convergence diagnostics more than they need speed).
#   * setup_only=true now also POPULATES Task Automation and stops. The user
#     inspects the 45 rows, sets Parallel Computing, and presses Execute.
#   * NEW: verify_completion_from_results() — post-hoc failure detection for
#     task_automation mode. ComTasks runs the cases, so Python never sees a
#     ComSim return code or b:warnA count and the failed-case report would
#     otherwise come back empty with all cases reported "solved". Each case's
#     result file is instead checked to reach t_stop within a tolerance of
#     2 x dtgrd_max. A short (or absent) result file means the case did not
#     complete. Cruder than the three-layer check — it cannot distinguish
#     "initial conditions failed" from "diverged at t=14 s" — but it keeps
#     the manifest and the OEM report honest.
#   * Parallel Computing settings are NOT scripted. Enable them once on the
#     Parallel Computing page of the Task Automation dialog; PowerFactory
#     remembers the setting per project. Note each parallel slot consumes a
#     PowerFactory licence: N slots gives an Nx speed-up, not 45x.
#
# CHANGELOG v3.1 -> v4.0
# ----------------------
#   * Shared plumbing moved to gcc_common.py (single source for both scripts)
#   * CSV export: FindColumn-based column mapping with runtime self-check;
#     case lookup scoped to this run's folder; stream-written; Release() per
#     case. (v3.1 positional col_idx+1 mapping could silently export the
#     wrong signal — see gcc_common.export_case_to_csv docstring.)
#   * NEW: JSON run manifest — the interface contract for the evaluation tool
#     (per-case uret / t_fault / t_clear / setpoints / status / reason)
#   * run_sim_only: pre-fault setpoints are now RE-APPLIED per case (v3.1
#     relied on op scenarios that were never saved -> all Q cases ran with
#     whatever Q was live in the network data)
#   * Op scenarios are saved (IntScenario.Save) after setpoints are written,
#     so cases are self-contained when opened manually
#   * Run folders use ISO timestamps (FRT_YYYY-MM-DD_HH-MM-SS); the latest-
#     folder lookup parses old DD-MM-YYYY names too (string sort on the old
#     format picked the wrong folder across month boundaries)
#   * Preflight: config, elements, base case, and output dir are ALL verified
#     BEFORE any run folder is created (v3.1 left empty FRT_* folders behind
#     on failure, which the run_sim_only lookup could then pick up)
#   * UI is guaranteed unfrozen on any error (gcc_common.pf_session)
#   * Plot x-ranges derived per case from t_fault / t_clear / recovery time
#     (v3.1 hardcoded 9-16 s)
#   * sim_timeout_s is no longer read — it never did anything; the
#     iopt_adapt=1 + dtgrd_max cap is the hang protection. Remove the key
#     from project_config.yaml.
# ==============================================================================

import sys
import os
import math
import time as clock

# ------------------------------------------------------------------------------
# Make gcc_common.py importable (it sits next to this script)
# ------------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcc_common as gcc

import powerfactory


# ------------------------------------------------------------------------------
# PATH TO PROJECT CONFIG
#   Set an absolute path here, OR leave as None to use the project_config.yaml
#   located in the same folder as this script.
# ------------------------------------------------------------------------------
CONFIG_FILE = r"C:\Projects\GCC Script Development\Codemasters\Working Scripts\project_config_update.yaml"
# CONFIG_FILE = None


# ------------------------------------------------------------------------------
# Maximum adaptive timestep [s] — solver hang protection.
#   With iopt_adapt=1, PowerFactory abandons a diverging case rather than
#   shrinking the timestep indefinitely. 10 ms resolves the fault transient
#   with ample margin.
#   NOTE: this makes the result time base NON-UNIFORM — the evaluation tool
#   must interpolate on time, never index by row.
# ------------------------------------------------------------------------------
DTGRD_MAX_S = 0.01


# ==============================================================================
# FAULT TYPE MAP  — grid code string -> PowerFactory i_shc integer
# ==============================================================================
FAULT_TYPE_MAP = {
    "three_phase":            0,
    "two_phase":              1,
    "single_phase_to_ground": 2,
    "two_phase_to_ground":    3,
}

FT_SHORT = {
    "three_phase":            "3PH",
    "two_phase":              "2PH",
    "single_phase_to_ground": "1PH",
    "two_phase_to_ground":    "2PHG",
}

# Variables recorded to the ElmRes result file for each study case —
# single authoritative definition lives in gcc_common.
RECORD_VARS = gcc.RECORD_VARS_FRT


# ==============================================================================
# 1.  STUDY CASE GENERATION
#     Derives all FRT cases automatically from the grid code file.
# ==============================================================================

def generate_study_cases(grid_code_cfg, sim_cfg, Pmax, Qmax, Qmin):
    """
    Generate the full list of FRT study cases from the grid code requirements.

    Reads fault_types and frt_scenarios from the fault_ride_through block
    (type_2 preferred, falls back to type_1). For each combination of
    fault type, Uret level, and Q condition (Q0 / Qmax / Qmin), one study
    case dict is produced.

    Returns (cases, frt_block) — the case list and the fault_ride_through
    block actually used (the manifest and plot ranges read from it).
    """
    frt = (grid_code_cfg
           .get("requirements_type_2", {})
           .get("fault_ride_through", {}))
    if not frt.get("applicable"):
        frt = (grid_code_cfg
               .get("requirements_type_1", {})
               .get("fault_ride_through", {}))
    if not frt.get("applicable"):
        return [], {}

    fault_types   = frt.get("fault_types", [])
    frt_scenarios = frt.get("frt_scenarios", [])

    if not fault_types or not frt_scenarios:
        return [], frt

    t_fault = float(sim_cfg["fault_start_s"])
    t_stop  = float(sim_cfg["t_stop_s"])

    q_conditions = [
        ("Q0",   0.0),
        ("Qmax", Qmax),
        ("Qmin", Qmin),
    ]

    cases = []
    for ft_str in fault_types:
        ft_int = FAULT_TYPE_MAP.get(ft_str)
        if ft_int is None:
            continue

        ft_short = FT_SHORT.get(ft_str, ft_str.upper())

        for scenario in frt_scenarios:
            uret         = float(scenario["uret_pu"])
            clr_duration = float(scenario["clearance_duration_s"])
            t_clear      = round(t_fault + clr_duration, 4)

            for q_label, qsetp in q_conditions:
                # Name format: "FRT 3PH 0.05 Q0"
                uret_str = f"{uret:.2f}".rstrip("0").rstrip(".")
                name = f"FRT {ft_short} {uret_str} {q_label}"

                cases.append({
                    "name":       name,
                    "fault_type": ft_str,
                    "ft_int":     ft_int,
                    "uret":       uret,
                    "t_fault":    t_fault,
                    "t_clear":    t_clear,
                    "t_stop":     t_stop,
                    "psetp":      Pmax,
                    "qsetp":      qsetp,
                    "usetp":      1.0,
                    "q_label":    q_label,
                })

    return cases, frt


# ==============================================================================
# 2.  RMS SIMULATION CONFIGURATION  (FRT-specific — stays in this script)
# ==============================================================================

def configure_rms(comInc, comSim, sim_cfg, ft_int, t_stop):
    """
    Configure ComInc and ComSim for an RMS simulation.
    Asymmetric faults (1ph-g, 2ph, 2ph-g) require unbalanced (rst) network
    representation. Three-phase faults use balanced (sym).
    """
    comInc.iopt_sim   = "rms"
    comInc.iopt_show  = 0
    comInc.iopt_adapt = 0
    comInc.dtgrd      = float(sim_cfg["integration_step_s"])
    comInc.tstart     = float(sim_cfg["t_start_s"])
    comInc.iopt_lt    = int(sim_cfg["a_stable_integration"])
    comInc.iopt_net   = "rst" if ft_int in (1, 2, 3) else "sym"
    comSim.tstop      = t_stop


# ==============================================================================
# 3.  FAULT IMPEDANCE + EVENT CREATION
# ==============================================================================

def compute_fault_impedance(uret, usetp, nom_Z, ratio_R_X):
    """
    Calculate fault impedance (R_f, X_f) [Ohm] to achieve the target retained
    voltage uret [p.u.] at the POC. Near-bolted values used when uret <= 0.01.

    NOTE: this is the pre-fault voltage-divider estimate. The plant's reactive
    current injection during the fault raises the achieved Uret above the
    target — the evaluation tool should report achieved vs target Uret per
    case (data is in the CSV: POC Pos-Seq Voltage during the fault window).
    """
    if uret <= 0.01:
        return 0.001, 0.01
    fault_X = (uret / (usetp - uret)) * nom_Z
    fault_R = fault_X * ratio_R_X
    return fault_R, fault_X


def create_fault_event(app, busbar, time_s, fault_type_int, R_f, X_f):
    """Create a single EvtShc fault event in the active study case."""
    evt_folder     = app.GetFromStudyCase("Simulation Events/Fault.IntEvt")
    event          = evt_folder.CreateObject("EvtShc", busbar.loc_name)
    event.p_target = busbar
    event.time     = time_s
    event.i_shc    = fault_type_int
    if fault_type_int != 4:   # clear events carry no impedance
        event.R_f = R_f
        event.X_f = X_f
    return event


# ==============================================================================
# 4.  RESULT VARIABLE RECORDING
# ==============================================================================

def add_recorded_results(elmRes, elm_objects, app):
    """
    Add variables to the ElmRes result object for the active study case.
    elm_objects: dict mapping element_key -> PF object
    """
    for elm_key, variable, _label in RECORD_VARS:
        elm = elm_objects.get(elm_key)
        if elm is None:
            app.PrintWarn(
                f"  Record: element '{elm_key}' not found — skipping '{variable}'"
            )
            continue
        elmRes.AddVariable(elm, variable)


# ==============================================================================
# 5.  PLOT PAGE SETUP
#     Two pages per study case:
#       PAGE 1 — 'POC'        : phase voltages, sequence voltages, power, current
#       PAGE 2 — 'GU Terminal': same layout using GU busbar + inverter signals
#
#     x-range is derived per case:  [t_fault - 1,  min(t_stop, t_clear + rec + 2)]
#     where rec = post_fault_recovery.recovery_time_s from the grid code
#     (falls back to 2 s if not defined).
# ==============================================================================

def setup_plot_pages(app, sc_cfg, recovery_time_s,
                     POC_BB, POC_BRK, GU_BB, Inverter):
    """Create and populate the two standard plot pages for the active study case."""

    desktop = app.GetGraphicsBoard()
    comInc  = app.GetFromStudyCase("ComInc")
    elmRes  = comInc.p_resvar

    t_fault = sc_cfg["t_fault"]
    t_clear = sc_cfg["t_clear"]
    t_stop  = sc_cfg["t_stop"]

    x_min = max(0.0, t_fault - 1.0)
    x_max = min(t_stop, t_clear + recovery_time_s + 2.0)
    x_refs = [t_fault, t_clear]

    # PAGE 1 — POC  (2x2 grid)
    page_poc = desktop.GetPage("POC", 1, "GrpPage")
    page_poc.SetResults(elmRes)

    _p_src = POC_BRK if POC_BRK else POC_BB

    poc_graphs = [
        gcc.make_graph(page_poc, "Curve plot",
                       [(POC_BB, "m:u:A", gcc.C_BLUE),
                        (POC_BB, "m:u:B", gcc.C_RED),
                        (POC_BB, "m:u:C", gcc.C_GREEN)],
                       row=0, col=0, x_refs=x_refs),
        gcc.make_graph(page_poc, "Curve plot(1)",
                       [(POC_BB, "m:u1", gcc.C_BLUE),
                        (POC_BB, "m:u2", gcc.C_RED)],
                       row=0, col=1, x_refs=x_refs),
        gcc.make_graph(page_poc, "Curve plot(2)",
                       [(_p_src, "m:Psum:bus1", gcc.C_TEAL),
                        (_p_src, "m:Qsum:bus1", gcc.C_BLUE)],
                       row=1, col=0, x_refs=x_refs),
        gcc.make_graph(page_poc, "Curve plot(3)",
                       [(_p_src, "m:i1P:bus1", gcc.C_TEAL),
                        (_p_src, "m:i1Q:bus1", gcc.C_ORANGE),
                        (_p_src, "m:i2Q:bus1", gcc.C_GREEN)],
                       row=1, col=1, x_refs=x_refs),
    ]

    try:
        page_poc.SetLayoutMode(2)
        page_poc.numLayoutColumns = 2
    except Exception:
        pass

    for _g in poc_graphs:
        gcc.set_xrange(_g, x_min, x_max, app)

    # PAGE 2 — GU Terminal  (2x2 grid)
    page_gu = desktop.GetPage("GU Terminal", 1, "GrpPage")
    page_gu.SetResults(elmRes)

    gu_graphs = [
        gcc.make_graph(page_gu, "Curve plot",
                       [(GU_BB, "m:u:A", gcc.C_BLUE),
                        (GU_BB, "m:u:B", gcc.C_RED),
                        (GU_BB, "m:u:C", gcc.C_GREEN)],
                       row=0, col=0, x_refs=x_refs),
        gcc.make_graph(page_gu, "Curve plot(1)",
                       [(GU_BB, "m:u1", gcc.C_BLUE),
                        (GU_BB, "m:u2", gcc.C_RED)],
                       row=0, col=1, x_refs=x_refs),
        gcc.make_graph(page_gu, "Curve plot(2)",
                       [(Inverter, "m:Psum:bus1", gcc.C_TEAL),
                        (Inverter, "m:Qsum:bus1", gcc.C_BLUE)],
                       row=1, col=0, x_refs=x_refs),
        gcc.make_graph(page_gu, "Curve plot(3)",
                       [(Inverter, "m:i1P:bus1", gcc.C_TEAL),
                        (Inverter, "m:i1Q:bus1", gcc.C_ORANGE),
                        (Inverter, "m:i2Q:bus1", gcc.C_GREEN)],
                       row=1, col=1, x_refs=x_refs),
    ]

    try:
        page_gu.SetLayoutMode(2)
        page_gu.numLayoutColumns = 2
    except Exception:
        pass

    for _h in gu_graphs:
        gcc.set_xrange(_h, x_min, x_max, app)

    app.Rebuild(2)


# ==============================================================================
# 5b.  TASK AUTOMATION (ComTasks)
#
#   Enables PARALLEL execution of the FRT case batch.
#
#   Why AppendStudyCase / AppendCommand and not CreateObject:
#   ComTasks rebuilds its internal child table as a side effect of each
#   CreateObject() call, so populating with CreateObject in a loop leaves
#   only the last case in the list. The Append* API writes the table
#   directly and returns 0/1 per call, so every row can be verified.
#
#   Command order per case: ComInc ("Calculation of Initial Conditions")
#   MUST be appended before ComSim ("Run Simulation").
# ==============================================================================

COMTASKS_NAME = "Task Automation"


def get_or_create_comtasks(app, sc_folder_root):
    """
    Find (or create) the ComTasks object at the Study Cases root — the
    location PowerFactory itself uses. The frequency script never touches
    ComTasks, so the FRT script owns this object exclusively.
    """
    existing = sc_folder_root.GetContents(f"{COMTASKS_NAME}.ComTasks")
    if existing:
        return existing[0]
    return sc_folder_root.CreateObject("ComTasks", COMTASKS_NAME)


def populate_task_automation(app, comtasks, study_cases, failed, sc_folder):
    """
    Populate the ComTasks object with every successfully-created study case
    in this run, each with its ComInc and ComSim commands.

    Cases already recorded in 'failed' (i.e. that failed during setup) are
    skipped — there is nothing to run for them.

    Returns (n_cases_added, n_commands_added).
    """
    # Clear any rows left over from a previous run
    try:
        comtasks.RemoveStudyCases()
    except Exception as e:
        app.PrintWarn(f"  Could not clear existing Task Automation rows: {e}")

    n_cases    = 0
    n_commands = 0

    for sc_cfg in study_cases:
        sc_name = sc_cfg["name"]

        if sc_name in failed:
            app.PrintWarn(f"  Task Automation: skipping failed case '{sc_name}'.")
            continue

        matches = sc_folder.GetContents(f"{sc_name}.IntCase")
        if not matches:
            app.PrintWarn(
                f"  Task Automation: '{sc_name}' not found in "
                f"'{sc_folder.loc_name}' — skipping."
            )
            continue
        study_case = matches[0]

        # --- Append the study case (returns 1 on success) ---
        if not comtasks.AppendStudyCase(study_case):
            app.PrintWarn(f"  Task Automation: failed to add case '{sc_name}'.")
            continue
        n_cases += 1

        # --- Fetch this case's ComInc and ComSim ---
        #     GetFromStudyCase requires the case to be active.
        study_case.Activate()
        comInc = app.GetFromStudyCase("ComInc")
        comSim = app.GetFromStudyCase("ComSim")

        # --- Append commands: ComInc BEFORE ComSim ---
        for cmd, label in ((comInc, "ComInc"), (comSim, "ComSim")):
            if cmd is None:
                app.PrintWarn(
                    f"  Task Automation: {label} not found for '{sc_name}'."
                )
                continue
            if comtasks.AppendCommand(cmd):
                n_commands += 1
            else:
                app.PrintWarn(
                    f"  Task Automation: failed to add {label} for '{sc_name}'."
                )

    # --- Verify against the object's own count ---
    try:
        reported = comtasks.GetNumberOfStudyCases()
        if reported != n_cases:
            app.PrintWarn(
                f"  Task Automation: appended {n_cases} cases but ComTasks "
                f"reports {reported}. Check the dialog before executing."
            )
    except Exception:
        pass

    return n_cases, n_commands


def verify_completion_from_results(app, study_cases, failed, sc_folder,
                                   dtgrd_max_s):
    """
    Post-hoc failure detection for task_automation mode.

    ComTasks runs the cases, so this script never sees a ComSim return code
    or a b:warnA count — without this check every case would be reported as
    "solved" in the manifest whether it converged or not.

    Each case's result file is checked to reach t_stop within a tolerance of
    2 x dtgrd_max (the time base is non-uniform under iopt_adapt=1, so the
    final sample lands NEAR t_stop, not exactly on it). A short or absent
    result file means the case did not complete.

    LIMITATION: this cannot distinguish "initial conditions failed" from
    "diverged at t=14 s", and it cannot see a case that ran to t_stop while
    producing physically unreliable results (b:warnA). It catches the cases
    that stopped early, which is the failure mode that matters most.

    Adds entries to 'failed' in place. Returns the number newly marked failed.
    """
    n_new_failures = 0
    tolerance = 2.0 * dtgrd_max_s

    for sc_cfg in study_cases:
        sc_name = sc_cfg["name"]
        if sc_name in failed:
            continue   # already failed at setup

        t_stop = float(sc_cfg["t_stop"])

        matches = sc_folder.GetContents(f"{sc_name}.IntCase")
        if not matches:
            failed[sc_name] = "Study case not found after Task Automation run."
            n_new_failures += 1
            continue

        matches[0].Activate()
        comInc = app.GetFromStudyCase("ComInc")
        elmRes = comInc.p_resvar

        last_t = None
        try:
            elmRes.Load()
            n_rows = elmRes.GetNumberOfRows()
            if n_rows > 0:
                err_t, last_t = elmRes.GetValue(n_rows - 1)   # time column
                if err_t:
                    last_t = None
        except Exception:
            last_t = None
        finally:
            try:
                elmRes.Release()
            except Exception:
                pass

        if last_t is None:
            failed[sc_name] = (
                "No result data — initial conditions or simulation failed "
                "under Task Automation."
            )
            n_new_failures += 1
            app.PrintWarn(f"  [{sc_name}] FAILED — no result data.")

        elif last_t < (t_stop - tolerance):
            failed[sc_name] = (
                f"Result file ends at t={last_t:.3f}s, expected {t_stop:.1f}s "
                f"(tolerance {tolerance:.3f}s) — case did not complete."
            )
            n_new_failures += 1
            app.PrintWarn(
                f"  [{sc_name}] FAILED — ended at t={last_t:.3f}s "
                f"of {t_stop:.1f}s."
            )

    return n_new_failures


# ==============================================================================
# 6.  PREFLIGHT
#     Everything that can fail is checked HERE, before any run folder or
#     scenario is created. If preflight raises, the PF project is untouched.
# ==============================================================================

def preflight(app, cfg, gc_cfg, sim_cfg, elem_cfg, out_dir, run_sim_only):
    """
    Verify config, model elements, base case, and output folder.

    Returns (elm_objects, refs) where:
      elm_objects — {elm_key: PF object} for result recording / CSV export
      refs        — dict of the individually named objects the main loop uses
    """
    app.PrintPlain("\nPreflight checks...")

    # --- 6.1 Required config keys ---------------------------------------------
    for key in ("fault_start_s", "t_stop_s", "integration_step_s",
                "t_start_s", "a_stable_integration"):
        if key not in sim_cfg:
            raise RuntimeError(f"Missing 'simulation.{key}' in project config.")
    for key in ("poc_busbar", "gu_busbar", "external_grid",
                "station_ctrl", "pfc_ctrl", "inverter"):
        if key not in elem_cfg:
            raise RuntimeError(f"Missing 'elements.{key}' in project config.")
    if float(sim_cfg["fault_start_s"]) >= float(sim_cfg["t_stop_s"]):
        raise RuntimeError(
            "simulation.fault_start_s must be smaller than simulation.t_stop_s."
        )
    app.PrintPlain("  [ok] Config keys")

    # --- 6.2 Model elements ----------------------------------------------------
    POC_BB   = gcc.find_element(app, "ElmTerm",    elem_cfg["poc_busbar"])
    GU_BB    = gcc.find_element(app, "ElmTerm",    elem_cfg["gu_busbar"])
    Ext_grid = gcc.find_element(app, "ElmXnet",    elem_cfg["external_grid"])
    SC       = gcc.find_element(app, "ElmStactrl", elem_cfg["station_ctrl"])
    PFC      = gcc.find_element(app, "ElmSecctrl", elem_cfg["pfc_ctrl"])
    POC_BRK  = (gcc.find_element(app, "ElmCoup", elem_cfg["poc_breaker"],
                                 required=False)
                if "poc_breaker" in elem_cfg else None)
    # Inverter may be ElmGenstat (PV/storage) or ElmSym (wind)
    Inverter = (gcc.find_element(app, "ElmGenstat", elem_cfg["inverter"],
                                 required=False)
                or gcc.find_element(app, "ElmSym", elem_cfg["inverter"]))

    app.PrintPlain(f"  [ok] POC busbar   : {POC_BB.loc_name}")
    app.PrintPlain(f"  [ok] GU busbar    : {GU_BB.loc_name}")
    if POC_BRK:
        app.PrintPlain(f"  [ok] POC breaker  : {POC_BRK.loc_name}")
    else:
        app.PrintWarn("  [--] POC breaker  : not configured — "
                      "POC power/current will be read from the busbar")
    app.PrintPlain(f"  [ok] Ext grid     : {Ext_grid.loc_name}")
    app.PrintPlain(f"  [ok] Station ctrl : {SC.loc_name}")
    app.PrintPlain(f"  [ok] PFC ctrl     : {PFC.loc_name}")
    app.PrintPlain(f"  [ok] Inverter     : {Inverter.loc_name}")

    # --- 6.3 Base case (only needed when creating cases) -----------------------
    sc_folder_root = gcc.get_folder(app, "study")
    base_case = None
    if not run_sim_only:
        base_case_name = elem_cfg.get("base_case", "GCC_BaseCase")
        base_matches   = sc_folder_root.GetContents(f"{base_case_name}.IntCase")
        if not base_matches:
            raise RuntimeError(
                f"Base case '{base_case_name}' not found in Study Cases folder.\n"
                "Check 'elements.base_case' in project_config.yaml."
            )
        base_case = base_matches[0]
        app.PrintPlain(f"  [ok] Base case    : {base_case.loc_name}")

    # --- 6.4 Output folder writable --------------------------------------------
    try:
        os.makedirs(out_dir, exist_ok=True)
        probe = os.path.join(out_dir, ".gcc_write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        raise RuntimeError(
            f"Output folder is not writable: {out_dir}\n{e}"
        )
    app.PrintPlain(f"  [ok] Output folder: {out_dir}")

    elm_objects = {
        "poc_busbar":  POC_BB,
        "poc_breaker": POC_BRK,
        "gu_busbar":   GU_BB,
        "inverter":    Inverter,
    }
    refs = {
        "POC_BB": POC_BB, "POC_BRK": POC_BRK, "GU_BB": GU_BB,
        "Inverter": Inverter, "Ext_grid": Ext_grid, "SC": SC, "PFC": PFC,
        "base_case": base_case, "sc_folder_root": sc_folder_root,
    }
    return elm_objects, refs


# ==============================================================================
# 7.  MAIN
# ==============================================================================

def _resolve_config_path():
    """CONFIG_FILE (file OR folder), else project_config.yaml next to script."""
    path = CONFIG_FILE or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "project_config.yaml")
    if os.path.isdir(path):     # folder given -> look for a config inside it
        for name in ("project_config.yaml", "project_config_update.yaml"):
            candidate = os.path.join(path, name)
            if os.path.isfile(candidate):
                return candidate
    return path


def _run(app):

    app.PrintPlain("=" * 68)
    app.PrintPlain("  GCC FRT Automation Script v4.0 — starting")
    app.PrintPlain("=" * 68)
    t0 = clock.perf_counter()

    # --------------------------------------------------------------------------
    # 7.1  Load project config and grid code
    # --------------------------------------------------------------------------
    config_path = _resolve_config_path()
    app.PrintPlain(f"\nLoading project config : {config_path}")
    cfg = gcc.load_yaml(config_path)

    proj_cfg  = cfg["project"]
    plant_cfg = cfg["plant"]
    sim_cfg   = cfg["simulation"]
    elem_cfg  = cfg["elements"]
    out_dir   = cfg["results"]["output_dir"]

    gc_path = cfg["grid_code"]["file"]
    if not os.path.isabs(gc_path):
        gc_path = os.path.join(os.path.dirname(config_path), gc_path)
    app.PrintPlain(f"Loading grid code      : {gc_path}")
    gc_cfg  = gcc.load_yaml(gc_path)
    gc_meta = gc_cfg.get("meta", {})

    # --------------------------------------------------------------------------
    # 7.2  Resolve plant values
    # --------------------------------------------------------------------------
    Pmax  = float(plant_cfg["pmax_mw"])
    U_POC = float(plant_cfg["poc_voltage_kv"])
    Qfac  = float(plant_cfg.get("q_factor", 0.33))
    Qmax  =  Qfac * Pmax
    Qmin  = -Qfac * Pmax
    In    =  Pmax / (math.sqrt(3) * U_POC)

    app.PrintPlain(f"\nProject  : {proj_cfg['name']}  |  {proj_cfg.get('number', '')}")
    app.PrintPlain(f"Client   : {proj_cfg.get('client', '?')}  |  {proj_cfg.get('country', '?')}")
    app.PrintPlain(f"Author   : {proj_cfg.get('author', '?')}  |  {proj_cfg.get('date', '?')}")
    app.PrintPlain(f"Grid code: {gc_meta.get('grid_code', {}).get('name', '?')}  "
                   f"Type {gc_meta.get('module_type', '?')}")
    app.PrintPlain(f"Plant    : {plant_cfg.get('technology', '?').upper()}  "
                   f"Type {plant_cfg.get('plant_type', '?')}  "
                   f"Module {plant_cfg.get('module_type', '?')}")
    app.PrintPlain(f"Pmax={Pmax:.1f} MW  U_POC={U_POC:.1f} kV  "
                   f"In={In:.4f} kA  Qmax=+/-{Qmax:.2f} Mvar")

    # --------------------------------------------------------------------------
    # 7.3  Script behaviour flags
    #
    #   setup_only     : create cases + populate Task Automation, run nothing
    #   run_sim_only   : reuse the latest run folder instead of creating cases
    #   execution_mode : how the cases are run when setup_only = false
    #                      'sequential'      — run from Python, three-layer
    #                                          convergence detection per case
    #                      'task_automation' — populate ComTasks and Execute
    #                                          (parallel-capable)
    #     execution_mode applies identically to the setup+run and the
    #     run_sim_only paths.
    # --------------------------------------------------------------------------
    setup_only   = bool(sim_cfg.get("setup_only",   True))
    run_sim_only = bool(sim_cfg.get("run_sim_only", False))

    execution_mode = str(sim_cfg.get("execution_mode", "sequential")).lower()
    if execution_mode not in ("sequential", "task_automation"):
        raise RuntimeError(
            f"simulation.execution_mode must be 'sequential' or "
            f"'task_automation', got '{execution_mode}'."
        )

    app.PrintPlain(f"Flags    : setup_only={setup_only}  "
                   f"run_sim_only={run_sim_only}  "
                   f"execution_mode={execution_mode}")

    # --------------------------------------------------------------------------
    # 7.4  Generate study cases from grid code
    # --------------------------------------------------------------------------
    study_cases, frt_block = generate_study_cases(gc_cfg, sim_cfg, Pmax, Qmax, Qmin)
    if not study_cases:
        app.PrintError(
            "No FRT study cases generated. Check the grid code file:\n"
            "  1. fault_ride_through.applicable must be true under requirements_type_2\n"
            "  2. fault_types must list at least one fault type\n"
            "  3. frt_scenarios must be present with at least one {uret_pu, clearance_duration_s} entry\n"
            f"  Grid code file: {gc_path}"
        )
        return

    # Post-fault recovery time — used for per-case plot x-ranges
    recovery_time_s = 2.0
    try:
        rec = (frt_block.get("post_fault_recovery", {})
                        .get("active_power", {})
                        .get("recovery_time_s"))
        if rec is not None:
            recovery_time_s = float(rec)
    except Exception:
        pass

    app.PrintPlain(f"\nGenerated {len(study_cases)} study cases from grid code.")
    for sc in study_cases:
        app.PrintPlain(
            f"  {sc['name']:35s}  "
            f"Uret={sc['uret']:.2f}pu  "
            f"t_clear={sc['t_clear']:.3f}s  "
            f"P={sc['psetp']:.1f}MW  Q={sc['qsetp']:.1f}Mvar"
        )

    # --------------------------------------------------------------------------
    # 7.5  PREFLIGHT — before anything is created in the project
    # --------------------------------------------------------------------------
    elm_objects, refs = preflight(
        app, cfg, gc_cfg, sim_cfg, elem_cfg, out_dir, run_sim_only
    )
    POC_BB   = refs["POC_BB"]
    POC_BRK  = refs["POC_BRK"]
    GU_BB    = refs["GU_BB"]
    Inverter = refs["Inverter"]
    Ext_grid = refs["Ext_grid"]
    SC       = refs["SC"]
    PFC      = refs["PFC"]
    base_case      = refs["base_case"]
    sc_folder_root = refs["sc_folder_root"]

    opscen_folder_root = gcc.get_folder(app, "scen")
    netdat_folder      = gcc.get_folder(app, "netdat")

    # --------------------------------------------------------------------------
    # 7.6  Run folder — create new (setup) or find latest (run_sim_only)
    # --------------------------------------------------------------------------
    if run_sim_only:
        sc_folder = gcc.find_latest_run_folder(sc_folder_root, "FRT")
        if sc_folder is None:
            app.PrintError(
                "run_sim_only=True but no FRT_* run folder found in Study Cases.\n"
                "Run the script with setup_only=True first to create study cases."
            )
            return
        run_tag = sc_folder.loc_name
        app.PrintPlain(f"\nRun folder : '{run_tag}'  [reusing existing — "
                       f"latest by parsed timestamp]")
    else:
        run_tag       = gcc.make_run_tag("FRT")
        sc_folder     = sc_folder_root.CreateObject("IntFolder", run_tag)
        opscen_folder = opscen_folder_root.CreateObject("IntFolder", run_tag)
        app.PrintPlain(f"\nRun folder : '{run_tag}'")

    # --------------------------------------------------------------------------
    # 7.7  Fault impedance base values
    # --------------------------------------------------------------------------
    ratio_R_X  = Ext_grid.rntxn
    extgrid_sc = Ext_grid.snss
    nom_Z      = (U_POC ** 2) / extgrid_sc
    app.PrintPlain(
        f"\nExt grid : Ssc={extgrid_sc:.1f} MVA  "
        f"R/X={ratio_R_X:.4f}  Z_nom={nom_Z:.6f} Ohm"
    )

    # --------------------------------------------------------------------------
    # 7.8  Ensure controllers are in service and in correct control mode
    # --------------------------------------------------------------------------
    SC.outserv  = 0
    PFC.outserv = 0
    SC.i_ctrl   = 1   # Reactive Power Control
    SC.qu_char  = 0   # Const. Q
    PFC.i_net   = 1   # Power-Frequency Control

    # --------------------------------------------------------------------------
    # 7.9  Create all operational scenarios up front
    # --------------------------------------------------------------------------
    opscen_objects = {}
    if not run_sim_only:
        app.PrintPlain(f"\nCreating {len(study_cases)} operational scenarios...")
        for sc_cfg in study_cases:
            name = sc_cfg["name"]
            existing = opscen_folder.GetContents(f"{name}.IntScenario")
            if existing:
                opscen_objects[name] = existing[0]
            else:
                opscen_objects[name] = opscen_folder.CreateObject("IntScenario", name)
        app.PrintPlain(f"  Done — {len(opscen_objects)} scenarios created.")

    # --------------------------------------------------------------------------
    # 7.10  Main loop — one iteration per study case
    #
    #   Each case is wrapped in a try/except so a single bad OEM model cannot
    #   kill the entire run. Failures are recorded in the 'failed' dict with
    #   the reason, and the loop continues to the next case.
    # --------------------------------------------------------------------------
    app.PrintPlain(f"\nProcessing study cases...")

    failed = {}   # {case_name: reason_string}
    passed = []   # [case_name]

    for sc_cfg in study_cases:
        sc_name  = sc_cfg["name"]
        ft_int   = sc_cfg["ft_int"]
        uret     = sc_cfg["uret"]
        t_fault  = sc_cfg["t_fault"]
        t_clear  = sc_cfg["t_clear"]
        t_stop   = sc_cfg["t_stop"]
        psetp    = sc_cfg["psetp"]
        qsetp    = sc_cfg["qsetp"]
        usetp    = sc_cfg["usetp"]

        app.PrintPlain(f"\n  [{sc_name}]")

        try:

            if not run_sim_only:

                # --------------------------------------------------------------
                # A. Copy base case and rename
                # --------------------------------------------------------------
                existing = sc_folder.GetContents(f"{sc_name}.IntCase")
                if existing:
                    study_case = existing[0]
                    app.PrintPlain("    [exists] Study case — reusing.")
                else:
                    study_case = sc_folder.AddCopy(base_case)
                    if study_case is None:
                        raise RuntimeError(
                            "AddCopy returned None — base case copy failed."
                        )
                    study_case.loc_name = sc_name
                    app.PrintPlain(f"    [copied] '{base_case.loc_name}' -> '{sc_name}'")

                # --------------------------------------------------------------
                # B. Activate case + scenario, THEN write setpoints, THEN save
                #    the scenario so the case is self-contained. (Writing
                #    setpoints before activating the scenario risks the
                #    activation overlaying them; never saving them was why
                #    run_sim_only lost the Q conditions in v3.1.)
                # --------------------------------------------------------------
                study_case.Activate()
                gcc.activate_grid(app, netdat_folder, Ext_grid.loc_name)

                opscen = opscen_objects.get(sc_name)
                if opscen:
                    opscen.Activate()

                PFC.psetp      = psetp
                SC.qsetp       = qsetp
                Ext_grid.usetp = usetp

                if opscen:
                    try:
                        opscen.Save()
                    except Exception as e:
                        app.PrintWarn(f"    Could not save op scenario: {e}")

                # --------------------------------------------------------------
                # C. Load flow + RMS settings
                # --------------------------------------------------------------
                ldf    = app.GetFromStudyCase("ComLDF")
                comInc = app.GetFromStudyCase("ComInc")
                comSim = app.GetFromStudyCase("ComSim")

                gcc.configure_loadflow(ldf)
                configure_rms(comInc, comSim, sim_cfg, ft_int, t_stop)

                # --------------------------------------------------------------
                # D. Fault events
                # --------------------------------------------------------------
                R_f, X_f = compute_fault_impedance(uret, usetp, nom_Z, ratio_R_X)
                app.PrintPlain(f"    Fault Z  : R={R_f:.5f}  X={X_f:.5f} Ohm")

                create_fault_event(app, POC_BB, t_fault, ft_int, R_f, X_f)  # apply
                create_fault_event(app, POC_BB, t_clear, 4,      R_f, X_f)  # clear

                # --------------------------------------------------------------
                # E. Record variables
                # --------------------------------------------------------------
                comInc.Execute()
                elmRes = comInc.p_resvar
                add_recorded_results(elmRes, elm_objects, app)

                # --------------------------------------------------------------
                # F. Plot pages
                # --------------------------------------------------------------
                setup_plot_pages(app, sc_cfg, recovery_time_s,
                                 POC_BB, POC_BRK, GU_BB, Inverter)
                app.PrintPlain("    Plot pages : POC, GU Terminal — configured.")

            else:
                # --------------------------------------------------------------
                # run_sim_only — activate the existing study case from the
                # run folder resolved in 7.6, then RE-APPLY the pre-fault
                # setpoints for this case. The setpoints travel in sc_cfg, so
                # the Q0/Qmax/Qmin conditions are always correct regardless of
                # what is stored in the scenario.
                # --------------------------------------------------------------
                existing = sc_folder.GetContents(f"{sc_name}.IntCase")
                if not existing:
                    raise RuntimeError(
                        f"Study case '{sc_name}' not found in '{run_tag}'. "
                        f"Re-run with setup_only=True to recreate study cases."
                    )
                existing[0].Activate()
                gcc.activate_grid(app, netdat_folder, Ext_grid.loc_name)

                PFC.psetp      = psetp
                SC.qsetp       = qsetp
                Ext_grid.usetp = usetp

            # ------------------------------------------------------------------
            # G0. Solver hang protection — applied to EVERY case, always.
            #
            #   iopt_adapt=1 with a dtgrd_max cap forces PF to give up on a
            #   diverging case instead of shrinking the timestep indefinitely.
            #   This is set during case CREATION, not only before running, so
            #   that a case executed later from the Task Automation dialog
            #   (setup_only mode) carries the protection too. Without it, a
            #   parallel batch can hang on one bad case with no Python running
            #   to notice.
            #
            #   NOTE: with iopt_adapt=1 the result time base is NON-UNIFORM.
            #   The evaluation tool must interpolate on time, never index
            #   by row number.
            # ------------------------------------------------------------------
            comInc = app.GetFromStudyCase("ComInc")
            comInc.iopt_adapt = 1      # automatic step size adaptation
            comInc.dtgrd_max  = DTGRD_MAX_S

            # ------------------------------------------------------------------
            # G. Run simulation — SEQUENTIAL mode only.
            #
            #   In task_automation mode the cases are executed by ComTasks
            #   after this loop (section 7.10b), so nothing is run here.
            #
            #   PowerFactory's Python API is single-threaded — comSim.Execute()
            #   must be called on the main thread and is blocking.
            #
            #   Failure layers:
            #     1. ComInc non-zero return : initial conditions failed
            #     2. ComSim non-zero return : simulation failed to complete
            #     3. GetTotalWarnA() > 0    : fatal solver divergence (b:warnA)
            # ------------------------------------------------------------------
            if not setup_only and execution_mode == "sequential":
                app.PrintPlain("    Running simulation...")
                comInc = app.GetFromStudyCase("ComInc")
                comSim = app.GetFromStudyCase("ComSim")

                # --- Layer 1: initial conditions ---
                err_inc = comInc.Execute()
                if err_inc:
                    raise RuntimeError(
                        f"Initial conditions did not converge (ComInc err={err_inc})."
                    )

                # --- Layer 2: run simulation ---
                err_sim = comSim.Execute()
                if err_sim:
                    raise RuntimeError(
                        f"Simulation did not complete (ComSim err={err_sim})."
                    )

                # --- Layer 3: fatal divergence check via b:warnA ---
                warn_a = comSim.GetTotalWarnA()
                if warn_a > 0:
                    raise RuntimeError(
                        f"Simulation diverged — b:warnA={warn_a} fatal "
                        f"solver warning(s). Results are unreliable."
                    )

                warn_b = comSim.GetTotalWarnB()
                warn_c = comSim.GetTotalWarnC()
                if warn_b > 0:
                    app.PrintWarn(
                        f"    b:warnB={warn_b} — major convergence issues, "
                        f"review results carefully."
                    )
                if warn_c > 0:
                    app.PrintWarn(
                        f"    b:warnC={warn_c} — minor convergence issues."
                    )

                app.PrintPlain("    Simulation complete.")

            passed.append(sc_name)

        except Exception as exc:
            # Record failure reason and move on — do NOT re-raise
            reason = str(exc)
            failed[sc_name] = reason
            app.PrintWarn(f"    FAILED — {reason}")
            app.PrintWarn(f"    Skipping plots and CSV for this case.")

            # Best-effort: deactivate the broken study case so PF stays clean
            try:
                app.GetActiveStudyCase().Deactivate()
            except Exception:
                pass

    # --------------------------------------------------------------------------
    # 7.10b  TASK AUTOMATION — populate, and execute if requested
    #
    #   setup_only=true                       -> populate, do NOT execute
    #   execution_mode='task_automation'      -> populate, then Execute
    #   execution_mode='sequential'           -> skip entirely (already run)
    # --------------------------------------------------------------------------
    comtasks_used = False
    n_ta_cases    = 0

    if setup_only or execution_mode == "task_automation":
        comtasks_used = True
        app.PrintPlain("\nPopulating Task Automation (ComTasks)...")

        comtasks = get_or_create_comtasks(app, sc_folder_root)
        n_ta_cases, n_ta_cmds = populate_task_automation(
            app, comtasks, study_cases, failed, sc_folder
        )
        app.PrintPlain(
            f"  Added {n_ta_cases} study case(s) and {n_ta_cmds} command(s) "
            f"(ComInc + ComSim per case)."
        )

        if setup_only:
            app.PrintPlain(
                "\n  setup_only=true — Task Automation populated but NOT executed.\n"
                "  Open  Study Cases > Task Automation,  check the "
                "'Parallel Computing' page,\n"
                "  then press Execute."
            )
        else:
            # ------------------------------------------------------------------
            # Execute the batch through ComTasks (parallel-capable).
            #
            #   Parallel Computing settings are NOT scripted — enable them once
            #   on the Parallel Computing page and PF remembers them per
            #   project. Each parallel slot consumes a PowerFactory licence.
            #
            #   ComTasks runs the cases, so the three-layer convergence check
            #   never sees them. verify_completion_from_results() below is the
            #   substitute; without it every case would report as "solved".
            # ------------------------------------------------------------------
            app.PrintPlain("\n  Executing Task Automation batch...")
            app.PrintPlain("  (Parallel Computing settings are taken from the "
                           "Task Automation dialog.)")

            err_ta = comtasks.Execute()
            if err_ta:
                app.PrintWarn(
                    f"  Task Automation returned error code {err_ta}. "
                    f"Individual case results are checked below."
                )
            else:
                app.PrintPlain("  Task Automation batch complete.")

            # --- Post-hoc failure detection ---
            app.PrintPlain("\n  Verifying case completion from result files...")
            n_new = verify_completion_from_results(
                app, study_cases, failed, sc_folder, DTGRD_MAX_S
            )
            if n_new:
                app.PrintWarn(f"  {n_new} case(s) did not complete — see summary.")
            else:
                app.PrintPlain("  All executed cases reached t_stop.")

            # Cases that survived are the ones not in 'failed'
            passed = [sc["name"] for sc in study_cases
                      if sc["name"] not in failed]

    # --------------------------------------------------------------------------
    # 7.11  Results export
    #        Handled EXCLUSIVELY by gcc_export_results.py (one CSV per case
    #        + export_manifest.json — the evaluation tool's contract).
    # --------------------------------------------------------------------------
    if not setup_only:
        app.PrintPlain(
            "\nResults export: run gcc_export_results.py (STUDY='FRT') to "
            "write one CSV per case."
        )

    # --------------------------------------------------------------------------
    # 7.12  Run manifest — the interface contract for the evaluation tool
    #        Written on EVERY run (setup-only included) so the planned case
    #        list and its parameters are always on disk.
    # --------------------------------------------------------------------------
    manifest_path = gcc.write_run_manifest(
        out_dir, run_tag,
        proj_cfg   = proj_cfg,
        plant_summary = {
            "pmax_mw":       Pmax,
            "u_poc_kv":      U_POC,
            "q_factor":      Qfac,
            "qmax_mvar":     Qmax,
            "qmin_mvar":     Qmin,
            "in_ka":         In,
            "ext_grid_ssc_mva": extgrid_sc,
            "ext_grid_r_x":  ratio_R_X,
        },
        gc_path     = gc_path,
        gc_meta     = gc_meta,
        study_cases = study_cases,
        failed      = failed,
        extra       = {
            "setup_only": setup_only,
            "run_sim_only": run_sim_only,
            "execution_mode": execution_mode,
            "task_automation_used": comtasks_used,
            "task_automation_cases": n_ta_cases,
            "failure_detection": (
                "three_layer (ComInc/ComSim return codes + b:warnA)"
                if execution_mode == "sequential" and not setup_only
                else "post_hoc_result_length" if comtasks_used and not setup_only
                else "none (setup only)"
            ),
            "script_version": "4.1",
        },
    )
    app.PrintPlain(f"\nRun manifest : {manifest_path}")

    # --------------------------------------------------------------------------
    # 7.13  Final summary
    # --------------------------------------------------------------------------
    elapsed  = round(clock.perf_counter() - t0, 2)
    n_total  = len(study_cases)
    n_failed = len(failed)
    # Derive from 'failed' so sequential and task_automation agree
    n_passed = n_total - n_failed

    app.PrintPlain("")
    app.PrintPlain("=" * 68)
    app.PrintPlain("  GCC FRT SIMULATION SUMMARY")
    app.PrintPlain("=" * 68)
    app.PrintPlain(f"  Run       : {run_tag}")
    app.PrintPlain(f"  Grid code : {gc_meta.get('grid_code', {}).get('name', '?')}  "
                   f"Type {gc_meta.get('module_type', '?')}")
    app.PrintPlain(f"  Runtime   : {elapsed}s")
    if setup_only:
        app.PrintPlain(f"  Mode      : setup only — Task Automation populated, "
                       f"not executed")
    else:
        app.PrintPlain(f"  Mode      : {execution_mode}")
    app.PrintPlain(f"  Cases     : {n_passed} / {n_total} solved")

    if comtasks_used and not setup_only:
        app.PrintPlain(
            "  NOTE      : failures detected post-hoc from result file length.\n"
            "              b:warnA divergence within a completed run is NOT\n"
            "              detected in task_automation mode — use sequential\n"
            "              mode for full three-layer convergence diagnostics."
        )

    if failed:
        app.PrintPlain("")
        app.PrintPlain(f"  FAILED CASES ({n_failed})  — share with OEM:")
        app.PrintPlain("  " + "-" * 64)
        for name, reason in failed.items():
            app.PrintPlain(f"  x  {name}")
            app.PrintPlain(f"       Reason : {reason}")
        app.PrintPlain("  " + "-" * 64)
    else:
        app.PrintPlain("")
        app.PrintPlain("  All cases solved successfully.")

    app.PrintPlain(f"\n  Manifest : {manifest_path}")
    if not setup_only:
        app.PrintPlain("  Export   : run gcc_export_results.py (STUDY='FRT')")
    app.PrintPlain("=" * 68)


def main():
    app = powerfactory.GetApplication()
    # pf_session guarantees EchoOn() + Rebuild() even if _run raises,
    # so a config typo can never leave the PF user interface frozen.
    with gcc.pf_session(app):
        try:
            _run(app)
        except Exception as exc:
            app.PrintError(f"GCC FRT script aborted: {exc}")
            raise


# ==============================================================================
# Entry point
# ==============================================================================
main()
