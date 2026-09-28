# ==============================================================================
# GCC FRT Automation Script
# ==============================================================================
# Reads ONE project config file (project_config.yaml) and the grid code file it
# points to, then automatically:
#   1. Derives all FRT study cases from the grid code
#      (fault type x retained voltage level x Q0/Qmax/Qmin)
#   2. Creates an operational scenario and a copy of GCC_BaseCase per case
#   3. Writes setpoints, fault events, RMS settings, recorded variables and
#      plot pages
#   4. Runs the simulations (unless setup_only = true), either one by one
#      from Python or as a Task Automation batch (parallel-capable)
#   5. Writes a JSON run manifest (the evaluation tool's input)
#
# Results are exported afterwards by gcc_export_results.py (STUDY = "FRT").
#
# Config flags (project_config.yaml -> simulation):
#   setup_only     : true  = create cases + populate Task Automation, run nothing
#   run_sim_only   : true  = re-run the cases of the latest FRT_* run folder
#   execution_mode : 'sequential'      — run from Python with three-layer
#                                        convergence detection (default)
#                    'task_automation' — run as a ComTasks batch; set Parallel
#                                        Computing once in the Task Automation
#                                        dialog (each slot uses a PF licence)
#
# The FRT requirements are read from requirements_<plant.plant_type> in the
# grid code file (type_1 = synchronous, type_2 = converter-based).
#
# Usage:
#   1. Edit project_config.yaml for your project
#   2. Set CONFIG_FILE below — a file, a folder containing
#      project_config(_update).yaml, or None for the one next to this script
#   3. Point a ComPython object at this script and run from PowerFactory
#
# Dependencies:
#   PyYAML         — "<PF Python>\python.exe" -m pip install pyyaml
#   gcc_common.py  — v2.0+, must sit in the SAME FOLDER as this script
#
# Author  : GCC Automation Team
# Version : 5.0
#
# CHANGELOG v4.2 -> v5.0
# ----------------------
#   * Shared plumbing (config/element lookup, run folders, case copy,
#     setpoints, simulation run, summary) now comes from gcc_common v2.0;
#     this script only holds what is FRT-specific.
#   * FRT requirements are taken from requirements_<plant_type> as set in
#     project_config.yaml, instead of always preferring type_2.
#   * Manifest records dtgrd_max_s (used by the exporter's completeness check)
#     and the correct script version.
#   * Fault impedance: raises a clear error if uret >= pre-fault voltage.
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


SCRIPT_VERSION = "5.0"

# ------------------------------------------------------------------------------
# PATH TO PROJECT CONFIG — file, folder, or None (project_config.yaml next to
# this script)
# ------------------------------------------------------------------------------
CONFIG_FILE = r"C:\Projects\GCC Script Development\Codemasters\Working Scripts\project_config_update.yaml"
# CONFIG_FILE = None

# Maximum adaptive timestep [s] — solver hang protection (see
# gcc.enable_hang_protection). 10 ms resolves the fault transient.
DTGRD_MAX_S = 0.01

# Grid code fault type -> (PowerFactory i_shc, short name used in case names)
FAULT_TYPES = {
    "three_phase":            (0, "3PH"),
    "two_phase":              (1, "2PH"),
    "single_phase_to_ground": (2, "1PH"),
    "two_phase_to_ground":    (3, "2PHG"),
}
I_SHC_CLEAR = 4          # EvtShc i_shc value for "clear short circuit"

RECORD_VARS = gcc.RECORD_VARS_FRT

ELEMENTS_REQUIRED = ("poc_busbar", "gu_busbar", "external_grid",
                     "station_ctrl", "pfc_ctrl", "inverter")
ELEMENTS_OPTIONAL = ("poc_breaker",)   # without it, POC P/Q come from the busbar

SIM_KEYS_REQUIRED = ("fault_start_s", "t_stop_s", "integration_step_s",
                     "t_start_s", "a_stable_integration")


# ==============================================================================
# 1.  STUDY CASE GENERATION — derived entirely from the grid code file
# ==============================================================================

def frt_requirements(gc_cfg, plant_type):
    """The fault_ride_through block for this plant type ('type_1'/'type_2')."""
    return (gc_cfg.get(f"requirements_{plant_type}", {})
                  .get("fault_ride_through", {}))


def generate_study_cases(frt, sim_cfg, plant):
    """
    One study case per (fault type, frt_scenario, Q condition) in the grid
    code's fault_ride_through block. Q conditions are Q0, Qmax and Qmin.
    Returns [] if the block is not applicable or incomplete.
    """
    if not frt.get("applicable"):
        return []

    t_fault = float(sim_cfg["fault_start_s"])
    t_stop  = float(sim_cfg["t_stop_s"])
    q_conditions = [("Q0",   0.0),
                    ("Qmax", plant["qmax_mvar"]),
                    ("Qmin", plant["qmin_mvar"])]

    cases = []
    for ft_str in frt.get("fault_types") or []:
        if ft_str not in FAULT_TYPES:
            continue
        ft_int, ft_short = FAULT_TYPES[ft_str]

        for scenario in frt.get("frt_scenarios") or []:
            uret     = float(scenario["uret_pu"])
            t_clear  = round(t_fault + float(scenario["clearance_duration_s"]), 4)
            uret_str = f"{uret:.2f}".rstrip("0").rstrip(".")

            for q_label, qsetp in q_conditions:
                cases.append({
                    "name":       f"FRT {ft_short} {uret_str} {q_label}",
                    "fault_type": ft_str,
                    "ft_int":     ft_int,
                    "uret":       uret,
                    "t_fault":    t_fault,
                    "t_clear":    t_clear,
                    "t_stop":     t_stop,
                    "psetp":      plant["pmax_mw"],
                    "qsetp":      qsetp,
                    "usetp":      1.0,
                    "q_label":    q_label,
                })
    return cases


# ==============================================================================
# 2.  CASE SETUP — RMS settings, fault events, plots
# ==============================================================================

def configure_rms(comInc, comSim, sim_cfg, ft_int, t_stop):
    """
    RMS simulation settings. Asymmetric faults (2ph, 1ph-g, 2ph-g) need the
    unbalanced (rst) network representation; three-phase faults use sym.
    """
    comInc.iopt_sim   = "rms"
    comInc.iopt_show  = 0
    comInc.iopt_adapt = 0
    comInc.dtgrd      = float(sim_cfg["integration_step_s"])
    comInc.tstart     = float(sim_cfg["t_start_s"])
    comInc.iopt_lt    = int(sim_cfg["a_stable_integration"])
    comInc.iopt_net   = "rst" if ft_int in (1, 2, 3) else "sym"
    comSim.tstop      = t_stop


def compute_fault_impedance(uret, usetp, nom_Z, ratio_R_X):
    """
    Fault impedance (R_f, X_f) [Ohm] giving the target retained voltage uret
    [p.u.] at the POC (voltage divider against the grid impedance nom_Z).
    Near-bolted values are used when uret <= 0.01.

    NOTE: this is the pre-fault estimate. The plant's reactive current
    injection raises the achieved Uret above the target — the evaluation tool
    should report achieved vs target Uret (POC Pos-Seq Voltage in the CSV).
    """
    if uret <= 0.01:
        return 0.001, 0.01
    if uret >= usetp:
        raise RuntimeError(
            f"Retained voltage {uret} pu must be below the pre-fault "
            f"voltage {usetp} pu."
        )
    fault_X = (uret / (usetp - uret)) * nom_Z
    return fault_X * ratio_R_X, fault_X


def create_fault_event(app, busbar, time_s, fault_type_int, R_f=None, X_f=None):
    """Create one EvtShc event in the active study case (clear events carry
    no impedance)."""
    evt_folder     = app.GetFromStudyCase("Simulation Events/Fault.IntEvt")
    event          = evt_folder.CreateObject("EvtShc", busbar.loc_name)
    event.p_target = busbar
    event.time     = time_s
    event.i_shc    = fault_type_int
    if fault_type_int != I_SHC_CLEAR:
        event.R_f = R_f
        event.X_f = X_f
    return event


def setup_plot_pages(app, case, recovery_time_s, elms):
    """
    Two 2x2 plot pages for the active study case:
      'POC'         : phase voltages, sequence voltages, P/Q, currents at POC
      'GU Terminal' : the same at the GU busbar / representative inverter
    x-range: [t_fault - 1, min(t_stop, t_clear + recovery + 2)], with
    reference lines at t_fault and t_clear.
    """
    x_min  = max(0.0, case["t_fault"] - 1.0)
    x_max  = min(case["t_stop"], case["t_clear"] + recovery_time_s + 2.0)
    x_refs = [case["t_fault"], case["t_clear"]]

    def fill_page(name, bus, pq_src):
        page = gcc.new_plot_page(app, name)
        graphs = [
            gcc.make_graph(page, "Curve plot",
                           [(bus, "m:u:A", gcc.C_BLUE),
                            (bus, "m:u:B", gcc.C_RED),
                            (bus, "m:u:C", gcc.C_GREEN)],
                           row=0, col=0, x_refs=x_refs),
            gcc.make_graph(page, "Curve plot(1)",
                           [(bus, "m:u1", gcc.C_BLUE),
                            (bus, "m:u2", gcc.C_RED)],
                           row=0, col=1, x_refs=x_refs),
            gcc.make_graph(page, "Curve plot(2)",
                           [(pq_src, "m:Psum:bus1", gcc.C_TEAL),
                            (pq_src, "m:Qsum:bus1", gcc.C_BLUE)],
                           row=1, col=0, x_refs=x_refs),
            gcc.make_graph(page, "Curve plot(3)",
                           [(pq_src, "m:i1P:bus1", gcc.C_TEAL),
                            (pq_src, "m:i1Q:bus1", gcc.C_ORANGE),
                            (pq_src, "m:i2Q:bus1", gcc.C_GREEN)],
                           row=1, col=1, x_refs=x_refs),
        ]
        gcc.set_2x2_layout(page)
        for g in graphs:
            gcc.set_xrange(g, x_min, x_max, app)

    fill_page("POC", elms["poc_busbar"],
              elms["poc_breaker"] or elms["poc_busbar"])
    fill_page("GU Terminal", elms["gu_busbar"], elms["inverter"])
    app.Rebuild(2)


def setup_case(app, case, ctx):
    """Create and configure one study case (setup path)."""
    study_case = gcc.copy_base_case(app, ctx["sc_folder"], ctx["base_case"],
                                    case["name"])
    gcc.activate_case(app, study_case, ctx["elms"], ctx["netdat"], case,
                      opscen=ctx["opscens"].get(case["name"]))

    comInc = app.GetFromStudyCase("ComInc")
    gcc.configure_loadflow(app.GetFromStudyCase("ComLDF"))
    configure_rms(comInc, app.GetFromStudyCase("ComSim"), ctx["sim_cfg"],
                  case["ft_int"], case["t_stop"])

    R_f, X_f = compute_fault_impedance(case["uret"], case["usetp"],
                                       ctx["nom_Z"], ctx["ratio_R_X"])
    app.PrintPlain(f"    Fault Z  : R={R_f:.5f}  X={X_f:.5f} Ohm")
    poc = ctx["elms"]["poc_busbar"]
    create_fault_event(app, poc, case["t_fault"], case["ft_int"], R_f, X_f)
    create_fault_event(app, poc, case["t_clear"], I_SHC_CLEAR)

    comInc.Execute()
    gcc.add_recorded_results(app, comInc.p_resvar, ctx["elms"], RECORD_VARS)

    setup_plot_pages(app, case, ctx["recovery_time_s"], ctx["elms"])
    app.PrintPlain("    Plot pages : POC, GU Terminal — configured.")


# ==============================================================================
# 3.  TASK AUTOMATION (ComTasks) — parallel-capable batch execution
#
#   Populated with ComTasks.AppendStudyCase / AppendCommand, NOT CreateObject:
#   CreateObject rebuilds the ComTasks child table on every call, so only the
#   last case would survive. The Append* API writes the table directly and
#   returns 0/1 per call, so every row can be verified.
#   Per case, ComInc MUST be appended before ComSim.
# ==============================================================================

COMTASKS_NAME = "Task Automation"


def populate_task_automation(app, study_cases, failed, sc_folder):
    """
    (Re)fill the ComTasks object at the Study Cases root with every case of
    this run that did not fail during setup. Returns (ComTasks, n_cases,
    n_commands).
    """
    comtasks = gcc.get_or_create(gcc.get_folder(app, "study"),
                                 COMTASKS_NAME, "ComTasks")
    try:
        comtasks.RemoveStudyCases()      # no stale rows from a previous run
    except Exception as e:
        app.PrintWarn(f"  Could not clear existing Task Automation rows: {e}")

    n_cases, n_commands = 0, 0
    for case in study_cases:
        name = case["name"]
        if name in failed:
            app.PrintWarn(f"  Task Automation: skipping failed case '{name}'.")
            continue

        matches = sc_folder.GetContents(f"{name}.IntCase")
        if not matches:
            app.PrintWarn(f"  Task Automation: '{name}' not found in "
                          f"'{sc_folder.loc_name}' — skipping.")
            continue
        if not comtasks.AppendStudyCase(matches[0]):
            app.PrintWarn(f"  Task Automation: failed to add case '{name}'.")
            continue
        n_cases += 1

        matches[0].Activate()            # GetFromStudyCase needs an active case
        for label in ("ComInc", "ComSim"):
            cmd = app.GetFromStudyCase(label)
            if cmd is None:
                app.PrintWarn(f"  Task Automation: {label} not found for '{name}'.")
            elif comtasks.AppendCommand(cmd):
                n_commands += 1
            else:
                app.PrintWarn(f"  Task Automation: failed to add {label} "
                              f"for '{name}'.")

    try:
        reported = comtasks.GetNumberOfStudyCases()
        if reported != n_cases:
            app.PrintWarn(f"  Task Automation: appended {n_cases} cases but "
                          f"ComTasks reports {reported}. Check the dialog "
                          f"before executing.")
    except Exception:
        pass

    return comtasks, n_cases, n_commands


def verify_completion_from_results(app, study_cases, failed, sc_folder):
    """
    Post-hoc failure detection for task_automation mode: ComTasks runs the
    cases, so Python never sees a ComSim return code or b:warnA count. A case
    whose result file does not reach t_stop (within 2 x dtgrd_max) did not
    complete and is added to 'failed'.

    LIMITATION: cannot tell "initial conditions failed" from "diverged at
    t=14 s", and cannot see a case that ran to t_stop with b:warnA
    divergence. Use sequential mode for full convergence diagnostics.

    Returns the number of newly failed cases.
    """
    n_new = 0
    for case in study_cases:
        name = case["name"]
        if name in failed:
            continue

        matches = sc_folder.GetContents(f"{name}.IntCase")
        if not matches:
            failed[name] = "Study case not found after Task Automation run."
            n_new += 1
            continue

        last_t = None
        try:
            with gcc.loaded_results(app, matches[0]) as elmRes:
                n_rows = elmRes.GetNumberOfRows()
                if n_rows > 0:
                    err_t, t = elmRes.GetValue(n_rows - 1)
                    last_t = None if err_t else t
        except Exception:
            last_t = None

        t_stop = float(case["t_stop"])
        if last_t is None:
            failed[name] = ("No result data — initial conditions or "
                            "simulation failed under Task Automation.")
            app.PrintWarn(f"  [{name}] FAILED — no result data.")
            n_new += 1
        elif not gcc.result_is_complete(last_t, t_stop, DTGRD_MAX_S):
            failed[name] = (
                f"Result file ends at t={last_t:.3f}s, expected {t_stop:.1f}s "
                f"(tolerance {2.0 * DTGRD_MAX_S:.3f}s) — case did not complete."
            )
            app.PrintWarn(f"  [{name}] FAILED — ended at t={last_t:.3f}s "
                          f"of {t_stop:.1f}s.")
            n_new += 1
    return n_new


# ==============================================================================
# 4.  MAIN
# ==============================================================================

def _run(app):
    gcc.print_banner(app, f"GCC FRT Automation Script v{SCRIPT_VERSION} — starting")
    t0 = clock.perf_counter()

    # --- Config and grid code -------------------------------------------------
    config_path = gcc.resolve_config_path(CONFIG_FILE, __file__)
    cfg, gc_cfg, gc_path = gcc.load_project(config_path, app)
    proj_cfg, sim_cfg, elem_cfg = cfg["project"], cfg["simulation"], cfg["elements"]
    plant_cfg = cfg["plant"]
    out_dir   = cfg["results"]["output_dir"]
    gc_meta   = gc_cfg.get("meta", {})
    gc_name   = (f"{gc_meta.get('grid_code', {}).get('name', '?')}  "
                 f"Type {gc_meta.get('module_type', '?')}")

    for key in SIM_KEYS_REQUIRED:
        if key not in sim_cfg:
            raise RuntimeError(f"Missing 'simulation.{key}' in project config.")
    if float(sim_cfg["fault_start_s"]) >= float(sim_cfg["t_stop_s"]):
        raise RuntimeError(
            "simulation.fault_start_s must be smaller than simulation.t_stop_s.")

    plant = gcc.plant_values(plant_cfg)
    plant["in_ka"] = plant["pmax_mw"] / (math.sqrt(3) * plant["u_poc_kv"])
    plant_type = plant_cfg.get("plant_type", "type_2")

    app.PrintPlain(f"\nProject  : {proj_cfg['name']}  |  {proj_cfg.get('number', '')}")
    app.PrintPlain(f"Client   : {proj_cfg.get('client', '?')}  |  {proj_cfg.get('country', '?')}")
    app.PrintPlain(f"Author   : {proj_cfg.get('author', '?')}  |  {proj_cfg.get('date', '?')}")
    app.PrintPlain(f"Grid code: {gc_name}")
    app.PrintPlain(f"Plant    : {plant_cfg.get('technology', '?').upper()}  "
                   f"Type {plant_type}  Module {plant_cfg.get('module_type', '?')}")
    app.PrintPlain(f"Pmax={plant['pmax_mw']:.1f} MW  U_POC={plant['u_poc_kv']:.1f} kV  "
                   f"In={plant['in_ka']:.4f} kA  Qmax=+/-{plant['qmax_mvar']:.2f} Mvar")

    # --- Flags ----------------------------------------------------------------
    setup_only     = bool(sim_cfg.get("setup_only",   True))
    run_sim_only   = bool(sim_cfg.get("run_sim_only", False))
    execution_mode = str(sim_cfg.get("execution_mode", "sequential")).lower()
    if execution_mode not in ("sequential", "task_automation"):
        raise RuntimeError(
            f"simulation.execution_mode must be 'sequential' or "
            f"'task_automation', got '{execution_mode}'.")
    app.PrintPlain(f"Flags    : setup_only={setup_only}  run_sim_only={run_sim_only}  "
                   f"execution_mode={execution_mode}")

    # --- Study cases from the grid code ---------------------------------------
    frt = frt_requirements(gc_cfg, plant_type)
    study_cases = generate_study_cases(frt, sim_cfg, plant)
    if not study_cases:
        app.PrintError(
            "No FRT study cases generated. Check the grid code file:\n"
            f"  1. fault_ride_through.applicable must be true under "
            f"requirements_{plant_type}\n"
            "  2. fault_types must list at least one fault type\n"
            "  3. frt_scenarios must be present with at least one "
            "{uret_pu, clearance_duration_s} entry\n"
            f"  Grid code file: {gc_path}")
        return

    rec = (frt.get("post_fault_recovery") or {}).get("active_power", {}) or {}
    recovery_time_s = float(rec["recovery_time_s"]) \
        if rec.get("recovery_time_s") is not None else 2.0

    app.PrintPlain(f"\nGenerated {len(study_cases)} study cases from grid code.")
    for sc in study_cases:
        app.PrintPlain(f"  {sc['name']:35s}  Uret={sc['uret']:.2f}pu  "
                       f"t_clear={sc['t_clear']:.3f}s  P={sc['psetp']:.1f}MW  "
                       f"Q={sc['qsetp']:.1f}Mvar")

    # --- Preflight: nothing is created in the project before this passes ------
    app.PrintPlain("\nPreflight checks...")
    elms = gcc.find_elements(app, elem_cfg, ELEMENTS_REQUIRED, ELEMENTS_OPTIONAL)
    base_case = None if run_sim_only else gcc.find_base_case(app, elem_cfg)
    gcc.check_output_dir(out_dir)
    app.PrintPlain(f"  [ok] Output folder: {out_dir}")

    # --- Run folder -----------------------------------------------------------
    run_tag, sc_folder, opscen_folder = gcc.open_run_folder(app, "FRT", run_sim_only)

    # --- Fault impedance base values ------------------------------------------
    ext_grid  = elms["external_grid"]
    ratio_R_X = ext_grid.rntxn
    ssc_mva   = ext_grid.snss
    nom_Z     = (plant["u_poc_kv"] ** 2) / ssc_mva
    app.PrintPlain(f"\nExt grid : Ssc={ssc_mva:.1f} MVA  R/X={ratio_R_X:.4f}  "
                   f"Z_nom={nom_Z:.6f} Ohm")

    gcc.set_controller_modes(elms)

    # --- Operational scenarios (setup path) -----------------------------------
    opscens = {}
    if not run_sim_only:
        app.PrintPlain(f"\nCreating {len(study_cases)} operational scenarios...")
        for sc in study_cases:
            opscens[sc["name"]] = gcc.get_or_create(opscen_folder, sc["name"],
                                                    "IntScenario")
        app.PrintPlain(f"  Done — {len(opscens)} scenarios created.")

    ctx = {"elms": elms, "base_case": base_case, "sc_folder": sc_folder,
           "opscens": opscens, "netdat": gcc.get_folder(app, "netdat"),
           "sim_cfg": sim_cfg, "nom_Z": nom_Z, "ratio_R_X": ratio_R_X,
           "recovery_time_s": recovery_time_s}

    # --- Main loop: one case at a time; a failure never stops the batch -------
    app.PrintPlain("\nProcessing study cases...")
    failed = {}   # {case_name: reason}
    run_now = not setup_only and execution_mode == "sequential"

    for case in study_cases:
        app.PrintPlain(f"\n  [{case['name']}]")
        try:
            if run_sim_only:
                # Re-apply this case's setpoints — they travel in the case
                # dict, so Q0/Qmax/Qmin are right whatever the scenario holds.
                gcc.activate_case(app, gcc.find_run_case(sc_folder, case["name"]),
                                  elms, ctx["netdat"], case)
            else:
                setup_case(app, case, ctx)

            gcc.enable_hang_protection(app.GetFromStudyCase("ComInc"), DTGRD_MAX_S)
            if run_now:
                gcc.run_simulation(app)
        except Exception as exc:
            gcc.record_failure(app, failed, case["name"], exc)

    # --- Task Automation: populate (setup_only) or populate + execute ---------
    comtasks_used, n_ta_cases = False, 0
    if setup_only or execution_mode == "task_automation":
        comtasks_used = True
        app.PrintPlain("\nPopulating Task Automation (ComTasks)...")
        comtasks, n_ta_cases, n_ta_cmds = populate_task_automation(
            app, study_cases, failed, sc_folder)
        app.PrintPlain(f"  Added {n_ta_cases} study case(s) and {n_ta_cmds} "
                       f"command(s) (ComInc + ComSim per case).")

        if setup_only:
            app.PrintPlain(
                "\n  setup_only=true — Task Automation populated but NOT executed.\n"
                "  Open  Study Cases > Task Automation,  check the "
                "'Parallel Computing' page,\n"
                "  then press Execute.")
        else:
            app.PrintPlain("\n  Executing Task Automation batch...")
            app.PrintPlain("  (Parallel Computing settings are taken from the "
                           "Task Automation dialog.)")
            err_ta = comtasks.Execute()
            if err_ta:
                app.PrintWarn(f"  Task Automation returned error code {err_ta}. "
                              f"Individual case results are checked below.")
            else:
                app.PrintPlain("  Task Automation batch complete.")

            app.PrintPlain("\n  Verifying case completion from result files...")
            n_new = verify_completion_from_results(app, study_cases, failed,
                                                   sc_folder)
            if n_new:
                app.PrintWarn(f"  {n_new} case(s) did not complete — see summary.")
            else:
                app.PrintPlain("  All executed cases reached t_stop.")

    if not setup_only:
        app.PrintPlain("\nResults export: run gcc_export_results.py (STUDY='FRT') "
                       "to write one CSV per case.")

    # --- Run manifest (written on every run, setup-only included) -------------
    if not setup_only and comtasks_used:
        failure_detection = "post_hoc_result_length"
    elif not setup_only:
        failure_detection = "three_layer (ComInc/ComSim return codes + b:warnA)"
    else:
        failure_detection = "none (setup only)"

    plant_summary = dict(plant, ext_grid_ssc_mva=ssc_mva, ext_grid_r_x=ratio_R_X)
    manifest_path = gcc.write_run_manifest(
        out_dir, run_tag, proj_cfg, plant_summary, gc_path, gc_meta,
        study_cases, failed,
        extra={
            "setup_only":            setup_only,
            "run_sim_only":          run_sim_only,
            "execution_mode":        execution_mode,
            "task_automation_used":  comtasks_used,
            "task_automation_cases": n_ta_cases,
            "failure_detection":     failure_detection,
            "dtgrd_max_s":           DTGRD_MAX_S,
            "script_version":        SCRIPT_VERSION,
        })
    app.PrintPlain(f"\nRun manifest : {manifest_path}")

    # --- Summary --------------------------------------------------------------
    mode = ("setup only — Task Automation populated, not executed"
            if setup_only else execution_mode)
    header = [f"Run       : {run_tag}",
              f"Grid code : {gc_name}",
              f"Runtime   : {round(clock.perf_counter() - t0, 2)}s",
              f"Mode      : {mode}"]
    if comtasks_used and not setup_only:
        header.append(
            "NOTE      : failures detected post-hoc from result file length.\n"
            "              b:warnA divergence within a completed run is NOT\n"
            "              detected in task_automation mode — use sequential\n"
            "              mode for full three-layer convergence diagnostics.")
    footer = [f"\n  Manifest : {manifest_path}"]
    if not setup_only:
        footer.append("  Export   : run gcc_export_results.py (STUDY='FRT')")
    gcc.print_summary(app, "GCC FRT SIMULATION SUMMARY", header,
                      len(study_cases), failed, footer)


def main():
    gcc.run_script("GCC FRT script", _run)


if __name__ == "__main__":
    main()
