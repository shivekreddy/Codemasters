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
# Version : 5.8
#
# CHANGELOG v5.7 -> v5.8
# ----------------------
#   * Folders come from ONE place: the project config 'paths: root:'. The run
#     manifest is written to <root>\Run Manifests (was results.output_dir).
#
# CHANGELOG v5.6 -> v5.7
# ----------------------
#   * Plot time axis: t_fault - 1 s to the simulation stop time
#     (simulation.t_stop_s, now 15 s) for every case.
#
# CHANGELOG v5.5 -> v5.6
# ----------------------
#   * Fixed y-axis ranges on the plot pages (time axis unchanged):
#       voltages 0 to 1.4 pu, currents -1 to 1.2 pu (POC and GU pages);
#       POC power per case: bottom = next multiple of 5 below
#       min(Q setpoint, 0), top = largest of P and |Q| setpoint + 10,
#       rounded up to a multiple of 5. GU power stays on auto scale.
#   * POC page power and currents plotted on the breaker's bus2 side
#     (export positive, as in the frequency plots). Plot only: the raw
#     data export keeps the bus1 variables.
#
# CHANGELOG v5.4 -> v5.5
# ----------------------
#   * Every plotted curve gets a legend label "<name> <variable> (<unit>)",
#     e.g. "Inverter 1 Total Active Power (MW)". The names per element are set
#     in the project config section plot_labels.
#
# CHANGELOG v5.3 -> v5.4
# ----------------------
#   * frt_reduced_p now applies to ALL FRT cases (Q0, Qmax and Qmin):
#     P = frt_reduced_p_factor x Pmax; Q stays at 0 / +/- q_factor x Pmax.
#
# CHANGELOG v5.2 -> v5.3
# ----------------------
#   * Option simulation.frt_reduced_p (1/0): the Qmax and Qmin cases run at
#     frt_reduced_p_factor x Pmax (default 0.93) so the inverters are not
#     overloaded at full Q; Q stays at +/- q_factor x Pmax. The P setpoint
#     (PFC.psetp) is then written in those cases' own scenarios. With the
#     option off, P is not touched (the base case's value is used).
#
# CHANGELOG v5.1 -> v5.2
# ----------------------
#   * FIX: Qmax / Qmin sign. SC.qsetp is now written in the plant convention
#     (QSETP_SIGN = +1): the Qmax case writes +q_factor x Pmax and the Qmin
#     case -q_factor x Pmax. (v4.3 - v5.1 wrote them the other way round.)
#
# CHANGELOG v5.0 -> v5.1
# ----------------------
#   * The simulation stop time of each case is set from simulation.t_stop_s
#     in the project config (ComSim.tstop only). v5.0 kept the base case's
#     stop time. Nothing else of the copied base case is changed.
#   * Removed the unused configure_rms() left over from v4.x.
#
# CHANGELOG v4.5 -> v5.0
# ----------------------
#   * The base case is copied AS IS. Per case the script now only adds the
#     fault events, the recorded variables and the plot pages, and sets the
#     station controller's Q setpoint (Qmax / Q0 / Qmin) in the case's own
#     scenario. It no longer touches: grid activation, load flow settings,
#     RMS settings (step size, network representation, start/stop time,
#     step size adaptation), controller modes and in-service flags, the P
#     and U setpoints, the external grid or voltage sources. Set all of
#     these up in the base case.
#   * Preflight reads the base case's RMS settings (read only) and warns if
#     the stop time is too short or asymmetric faults would run balanced.
#
# CHANGELOG v4.4 -> v4.5
# ----------------------
#   * Two-plant models: elements.gu_busbar1 / inverter1 (plant 1) and
#     gu_busbar2 / inverter2 (plant 2, null for a one-plant model). Each
#     plant gets its own GU page ("GU Terminal 1" / "GU Terminal 2"; one
#     plant keeps "GU Terminal"), and plant 2 is recorded for the export.
#     The old keys gu_busbar / inverter still work as plant 1.
#
# CHANGELOG v4.3 -> v4.4
# ----------------------
#   * Fault events are named "Fault" (inception) and "Fault Clearance"
#     instead of the busbar name, and the clearance is set to clear
#     immediately (i_clearShc = 1) instead of at zero crossing.
#
# CHANGELOG v4.2 -> v4.3
# ----------------------
#   * FIX: Qmax / Qmin were written to the station controller with the
#     opposite sign. SC.qsetp is now written as QSETP_SIGN x qsetp, so the
#     Qmax case writes -q_factor x Pmax. Case lists and the manifest keep the
#     plant convention (Qmax = +q_factor x Pmax, generation positive), which
#     is what frt_check.py compares the measured pre-fault Q against.
#

import sys
import os
import math
import time as clock

# ------------------------------------------------------------------------------
# Make gcc_common.py importable (it sits next to this script)
# ------------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# PowerFactory keeps imported modules in memory for the whole session:
# drop any cached copy so gcc_common.py is always loaded fresh from THIS
# folder (not an older version, or a copy from another folder).
sys.modules.pop("gcc_common", None)
import gcc_common as gcc

import powerfactory


# ------------------------------------------------------------------------------
# PATH TO PROJECT CONFIG
#   Set an absolute path (file or folder) here, OR leave as None to use the
#   project_config.yaml / project_config_update.yaml located in the same
#   folder as this script.
# ------------------------------------------------------------------------------
CONFIG_FILE = None


# ------------------------------------------------------------------------------
# Maximum adaptive timestep [s] — solver hang protection.
#   With iopt_adapt=1, PowerFactory abandons a diverging case rather than
#   shrinking the timestep indefinitely. 10 ms resolves the fault transient
#   with ample margin.
#   NOTE: this makes the result time base NON-UNIFORM — the evaluation tool
#   must interpolate on time, never index by row.
# ------------------------------------------------------------------------------
DTGRD_MAX_S = 0.01


# ------------------------------------------------------------------------------
# Station controller Q setpoint sign.
#   The case Q values follow the plant convention (Qmax = +q_factor x Pmax).
#   ElmStactrl.qsetp uses the same sign, so Qmax is written as +q_factor x Pmax
#   and Qmin as -q_factor x Pmax. Set to -1 for a controller that uses the
#   opposite sign.
# ------------------------------------------------------------------------------
QSETP_SIGN = +1

# ------------------------------------------------------------------------------
# Plot y-axis ranges (the time axis is set per case from the fault times)
# ------------------------------------------------------------------------------
Y_RANGE_VOLTAGE = (0.0, 1.4)    # phase and sequence voltages [pu]
Y_RANGE_CURRENT = (-1.0, 1.2)   # active / reactive currents [pu]
PQ_MARGIN_MW    = 10.0          # POC power: top = largest setpoint + margin
PQ_STEP_MW      = 5.0           # POC power: axis limits on multiples of this

# POC page power and currents are plotted on the POC breaker's bus2 side:
# export positive (bus1 is load oriented, export negative). These are
# recorded for the plots only; the raw data export keeps the bus1 variables.
POC_PLOT_SIDE = "bus2"
POC_PLOT_VARS = [("poc_breaker", f"m:{v}:{POC_PLOT_SIDE}", None)
                 for v in ("Psum", "Qsum", "i1P", "i1Q", "i2Q")]


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

def frt_reduced_p(sim_cfg):
    """
    (enabled, factor) from simulation.frt_reduced_p (1/0) and
    simulation.frt_reduced_p_factor (default 0.93).
    """
    enabled = int(sim_cfg.get("frt_reduced_p", 0) or 0) == 1
    factor  = float(sim_cfg.get("frt_reduced_p_factor", 0.93))
    if enabled and not 0.0 < factor <= 1.0:
        raise RuntimeError("simulation.frt_reduced_p_factor must be between "
                           f"0 and 1, got {factor}.")
    return enabled, factor


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

    # All cases at reduced P when simulation.frt_reduced_p = 1
    reduced_p, p_factor = frt_reduced_p(sim_cfg)

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
                    "psetp":      p_factor * Pmax if reduced_p else Pmax,
                    "p_reduced":  reduced_p,
                    "qsetp":      qsetp,
                    "usetp":      1.0,
                    "q_label":    q_label,
                })

    return cases, frt


# ==============================================================================
# 2.  BASE CASE RMS SETTINGS — read only (the script never changes them)
# ==============================================================================

def check_base_case_rms(app, base_case, study_cases, recovery_time_s):
    """
    Read the base case's RMS settings (without activating or changing
    anything) and warn where they do not suit the FRT cases:
      * the cases' stop time (simulation.t_stop_s) must reach the latest
        clearance + recovery time
      * asymmetric faults need an unbalanced (rst) RMS simulation
    """
    comInc = (base_case.GetContents("*.ComInc") or [None])[0]
    comSim = (base_case.GetContents("*.ComSim") or [None])[0]
    if comInc is None or comSim is None:
        app.PrintWarn("  [--] Base RMS     : no ComInc/ComSim in the base case; "
                      "set up and run an RMS simulation there first")
        return

    net = str(comInc.iopt_net)
    app.PrintPlain(f"  [ok] Base RMS     : {'unbalanced' if net == 'rst' else 'balanced'}"
                   f", step {float(comInc.dtgrd) * 1000:g} ms (stop time "
                   f"{float(comSim.tstop):g} s in the base case; the FRT cases "
                   f"use t_stop_s)")

    t_stop = study_cases[0]["t_stop"]
    needed = max(c["t_clear"] for c in study_cases) + recovery_time_s
    if t_stop < needed:
        app.PrintWarn(f"  [!!] Stop time    : t_stop_s = {t_stop:g} s is before the "
                      f"latest clearance + recovery ({needed:g} s). Increase "
                      f"simulation.t_stop_s in the project config.")
    if net != "rst" and any(c["ft_int"] in (1, 2, 3) for c in study_cases):
        app.PrintWarn("  [!!] Base RMS     : the base case is set to a BALANCED RMS "
                      "simulation, but there are asymmetric (1PH/2PH) faults. Set "
                      "it to unbalanced (3-phase) in the base case.")


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


FAULT_EVENT_NAME = "Fault"             # fault inception event
CLEAR_EVENT_NAME = "Fault Clearance"   # fault clearance event
I_SHC_CLEAR      = 4                   # EvtShc i_shc: clear short-circuit
I_CLEARSHC_IMMEDIATE = 1               # EvtShc i_clearShc: 0 = at zero crossing,
                                       #                    1 = immediately


def create_fault_event(app, busbar, time_s, fault_type_int, R_f, X_f):
    """
    Create a single EvtShc event in the active study case: the fault
    inception ("Fault") or, for fault_type_int = 4, the clearance
    ("Fault Clearance", cleared immediately rather than at zero crossing).
    """
    is_clear       = fault_type_int == I_SHC_CLEAR
    evt_folder     = app.GetFromStudyCase("Simulation Events/Fault.IntEvt")
    event          = evt_folder.CreateObject(
        "EvtShc", CLEAR_EVENT_NAME if is_clear else FAULT_EVENT_NAME)
    event.p_target = busbar
    event.time     = time_s
    event.i_shc    = fault_type_int
    if is_clear:
        event.i_clearShc = I_CLEARSHC_IMMEDIATE
    else:                     # clear events carry no impedance
        event.R_f = R_f
        event.X_f = X_f
    return event


# ==============================================================================
# 4.  RESULT VARIABLE RECORDING
# ==============================================================================

def add_recorded_results(elmRes, elm_objects, app, record_vars=RECORD_VARS):
    """
    Add variables to the ElmRes result object for the active study case.
    elm_objects: dict mapping element_key -> PF object
    record_vars: gcc.record_vars_frt(elem_cfg) — includes plant 2 if set
    """
    for elm_key, variable, _label in record_vars:
        elm = elm_objects.get(elm_key)
        if elm is None:
            app.PrintWarn(
                f"  Record: element '{elm_key}' not found — skipping '{variable}'"
            )
            continue
        elmRes.AddVariable(elm, variable)


def poc_pq_range(psetp, qsetp, margin=None, step=None):
    """
    y-axis range [MW / Mvar] of the POC power graph for one case:
      bottom = next multiple of `step` strictly below min(Q setpoint, 0), so
               a P dip to zero during the fault stays visible
               (Qmin -21.47 -> -25, Q0 / Qmax -> -5, -65 -> -70)
      top    = largest of P and |Q| setpoint + `margin`, rounded up to a
               multiple of `step` (P 60.5 MW -> 75)
    """
    margin = PQ_MARGIN_MW if margin is None else margin
    step   = PQ_STEP_MW if step is None else step
    low_val = min(float(qsetp), 0.0)
    bottom  = math.floor(low_val / step + 1e-9) * step
    if bottom >= low_val - 1e-9:
        bottom -= step
    top = math.ceil((max(float(psetp), abs(float(qsetp))) + margin) / step - 1e-9) * step
    return bottom, top


# ==============================================================================
# 5.  PLOT PAGE SETUP
#     One POC page plus one GU page per plant:
#       'POC'         : phase voltages, sequence voltages, power, current
#       'GU Terminal' : same layout using the plant's GU busbar + inverter
#                       ('GU Terminal 1' / 'GU Terminal 2' for two plants)
#
#     x-range per case:  [t_fault - 1,  t_stop]  (t_stop = simulation.t_stop_s)
# ==============================================================================

def gu_page_names(n_plants):
    """GU page names: 'GU Terminal' for one plant, numbered for two."""
    if n_plants == 1:
        return ["GU Terminal"]
    return [f"GU Terminal {i}" for i in range(1, n_plants + 1)]


def setup_plot_pages(app, sc_cfg, recovery_time_s, POC_BB, POC_BRK, plants,
                     names):
    """
    Create and populate the POC page and one GU page per plant for the
    active study case. plants: [(GU busbar, inverter), ...]
    names: legend names from gcc.plot_label_names() — every curve is labelled
    "<name> <variable> (<unit>)".
    """

    desktop = app.GetGraphicsBoard()
    comInc  = app.GetFromStudyCase("ComInc")
    elmRes  = comInc.p_resvar

    t_fault = sc_cfg["t_fault"]
    t_clear = sc_cfg["t_clear"]
    t_stop  = sc_cfg["t_stop"]

    x_min = max(0.0, t_fault - 1.0)
    x_max = t_stop                       # whole run after the fault (to t_stop_s)
    x_refs = [t_fault, t_clear]

    def graph(page, title, curves, row, col):
        """curves: [(element, legend name, variable, colour), ...]"""
        return gcc.make_graph(
            page, title, [(elm, var, colour) for elm, _, var, colour in curves],
            row=row, col=col, x_refs=x_refs,
            labels=[gcc.curve_label(name, var) for _, name, var, _ in curves])

    def fill_page(page_name, bus, bus_name, src, src_name, side="bus1",
                  pq_range=None):
        """
        2x2 page: phase voltages, sequence voltages, P/Q, currents.
        side: bus side of src for power and currents. pq_range: (min, max)
        of the P/Q graph, or None to leave it on auto scale.
        """
        page = desktop.GetPage(page_name, 1, "GrpPage")
        page.SetResults(elmRes)
        graphs = [
            graph(page, "Curve plot",
                  [(bus, bus_name, "m:u:A", gcc.C_BLUE),
                   (bus, bus_name, "m:u:B", gcc.C_RED),
                   (bus, bus_name, "m:u:C", gcc.C_GREEN)], 0, 0),
            graph(page, "Curve plot(1)",
                  [(bus, bus_name, "m:u1", gcc.C_BLUE),
                   (bus, bus_name, "m:u2", gcc.C_RED)], 0, 1),
            graph(page, "Curve plot(2)",
                  [(src, src_name, f"m:Psum:{side}", gcc.C_TEAL),
                   (src, src_name, f"m:Qsum:{side}", gcc.C_BLUE)], 1, 0),
            graph(page, "Curve plot(3)",
                  [(src, src_name, f"m:i1P:{side}", gcc.C_TEAL),
                   (src, src_name, f"m:i1Q:{side}", gcc.C_ORANGE),
                   (src, src_name, f"m:i2Q:{side}", gcc.C_GREEN)], 1, 1),
        ]
        try:
            page.SetLayoutMode(2)
            page.numLayoutColumns = 2
        except Exception:
            pass
        for g in graphs:
            gcc.set_xrange(g, x_min, x_max, app)
        gcc.set_yrange(graphs[0], *Y_RANGE_VOLTAGE, app)
        gcc.set_yrange(graphs[1], *Y_RANGE_VOLTAGE, app)
        if pq_range is not None:
            gcc.set_yrange(graphs[2], *pq_range, app)
        gcc.set_yrange(graphs[3], *Y_RANGE_CURRENT, app)

    # POC page: busbar voltages + breaker power and currents (export positive)
    fill_page("POC", POC_BB, names["poc_busbar"], POC_BRK, names["poc_breaker"],
              side=POC_PLOT_SIDE,
              pq_range=poc_pq_range(sc_cfg["psetp"], sc_cfg["qsetp"]))

    # GU pages — one per plant
    for n, (page_name, (GU_BB, Inverter)) in enumerate(
            zip(gu_page_names(len(plants)), plants), start=1):
        fill_page(page_name, GU_BB, names[f"gu_busbar{n}"],
                  Inverter, names[f"inverter{n}"])

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
    for key in ("fault_start_s", "t_stop_s"):
        if key not in sim_cfg:
            raise RuntimeError(f"Missing 'simulation.{key}' in project config.")
    for key in ("poc_busbar", "poc_breaker", "external_grid",
                "station_ctrl", "pfc_ctrl"):
        if key not in elem_cfg:
            raise RuntimeError(f"Missing 'elements.{key}' in project config.")
    plant_names = gcc.plant_elements(elem_cfg)   # [(gu_busbar, inverter), ...]
    if float(sim_cfg["fault_start_s"]) >= float(sim_cfg["t_stop_s"]):
        raise RuntimeError(
            "simulation.fault_start_s must be smaller than simulation.t_stop_s."
        )
    app.PrintPlain("  [ok] Config keys")

    # --- 6.2 Model elements ----------------------------------------------------
    POC_BB   = gcc.find_element(app, "ElmTerm",    elem_cfg["poc_busbar"])
    Ext_grid = gcc.find_element(app, "ElmXnet",    elem_cfg["external_grid"])
    SC       = gcc.find_element(app, "ElmStactrl", elem_cfg["station_ctrl"])
    PFC      = gcc.find_element(app, "ElmSecctrl", elem_cfg["pfc_ctrl"])
    POC_BRK  = gcc.find_element(app, "ElmCoup", elem_cfg["poc_breaker"],
                                required=False)
    if POC_BRK is None:
        # POC P/Q and currents are read from the breaker, so this is a hard
        # error — but list the breakers that DO exist so the correct
        # loc_name can be copied straight into the config.
        names = sorted(o.loc_name for o in
                       (app.GetCalcRelevantObjects("*.ElmCoup") or []))
        app.PrintError(
            f"  POC breaker '{elem_cfg['poc_breaker']}' (ElmCoup) not found. "
            f"{len(names)} calculation-relevant ElmCoup object(s) in the "
            f"active grids:"
        )
        for n in names[:50]:
            app.PrintPlain(f"      '{n}'")
        if len(names) > 50:
            app.PrintPlain(f"      ... and {len(names) - 50} more")
        raise RuntimeError(
            f"Element not found — class: ElmCoup, loc_name: "
            f"'{elem_cfg['poc_breaker']}'. Set 'elements.poc_breaker' to one "
            f"of the names listed above."
        )
    # One (GU busbar, inverter) pair per plant.
    # Inverter may be ElmGenstat (PV/storage) or ElmSym (wind).
    plants = []
    for gu_name, inv_name in plant_names:
        gu_bb    = gcc.find_element(app, "ElmTerm", gu_name)
        inverter = (gcc.find_element(app, "ElmGenstat", inv_name, required=False)
                    or gcc.find_element(app, "ElmSym", inv_name))
        plants.append((gu_bb, inverter))

    app.PrintPlain(f"  [ok] POC busbar   : {POC_BB.loc_name}")
    app.PrintPlain(f"  [ok] POC breaker  : {POC_BRK.loc_name}")
    app.PrintPlain(f"  [ok] Ext grid     : {Ext_grid.loc_name}")
    app.PrintPlain(f"  [ok] Station ctrl : {SC.loc_name}")
    app.PrintPlain(f"  [ok] PFC ctrl     : {PFC.loc_name}")
    for i, (gu_bb, inverter) in enumerate(plants, start=1):
        app.PrintPlain(f"  [ok] Plant {i}      : GU busbar '{gu_bb.loc_name}', "
                       f"inverter '{inverter.loc_name}'")

    # --- 6.3 Base case (only needed when creating cases) -----------------------
    sc_folder_root = gcc.get_folder(app, "study")
    base_case = None
    base_scenario = None
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

        # The base case's operation scenario holds its operational data
        # (external grid in service, voltage sources off, ...). Each case's
        # scenario is created as a COPY of it, so that data is kept.
        base_case.Activate()
        base_scenario = app.GetActiveScenario()
        base_case.Deactivate()
        if base_scenario is not None:
            app.PrintPlain(f"  [ok] Base scenario: {base_scenario.loc_name}")
        else:
            app.PrintWarn("  [--] Base scenario: none active in the base case; "
                          "case scenarios start empty")

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
        "gu_busbar":   plants[0][0],
        "inverter":    plants[0][1],
    }
    if len(plants) > 1:
        elm_objects["gu_busbar2"] = plants[1][0]
        elm_objects["inverter2"]  = plants[1][1]
    refs = {
        "POC_BB": POC_BB, "POC_BRK": POC_BRK, "plants": plants,
        "Ext_grid": Ext_grid, "SC": SC, "PFC": PFC,
        "base_case": base_case, "base_scenario": base_scenario,
        "sc_folder_root": sc_folder_root,
    }
    return elm_objects, refs


# ==============================================================================
# 7.  MAIN
# ==============================================================================

CONFIG_SECTIONS = ("project", "plant", "grid_code", "elements", "paths",
                   "simulation")


def check_config_sections(cfg, config_path):
    """
    Clear error if a required top-level section is missing from the project
    config — usually an indentation slip (a section name must start in
    column 1) or a section that was deleted/commented out while editing.
    """
    if not isinstance(cfg, dict):
        raise RuntimeError(f"Project config is empty or not a YAML mapping:\n"
                           f"  {config_path}")
    missing = [k for k in CONFIG_SECTIONS if not isinstance(cfg.get(k), dict)]
    if missing:
        nested = [f"'{k}' (inside '{parent}')" for parent, v in cfg.items()
                  if isinstance(v, dict) for k in missing if k in v]
        raise RuntimeError(
            f"Section(s) {missing} not found at the top level of the project "
            f"config:\n  {config_path}\n"
            f"Top-level sections found: {list(cfg)}\n"
            + (f"Found indented under another section: {', '.join(nested)} — "
               f"remove the indentation so it starts in column 1.\n"
               if nested else "")
            + "Each section name (e.g. 'elements:') must start in column 1, "
              "with its entries indented below it.")


def _resolve_config_path():
    """CONFIG_FILE (file OR folder), else the config next to this script."""
    path = CONFIG_FILE or os.path.dirname(os.path.abspath(__file__))
    if os.path.isdir(path):     # folder given -> look for a config inside it
        for name in ("project_config.yaml", "project_config_update.yaml"):
            candidate = os.path.join(path, name)
            if os.path.isfile(candidate):
                return candidate
    return path


def _run(app):

    app.PrintPlain("=" * 68)
    app.PrintPlain("  GCC FRT Automation Script v5.8 — starting")
    app.PrintPlain("=" * 68)
    t0 = clock.perf_counter()

    # --------------------------------------------------------------------------
    # 7.1  Load project config and grid code
    # --------------------------------------------------------------------------
    config_path = _resolve_config_path()
    app.PrintPlain(f"\nLoading project config : {config_path}")
    cfg = gcc.load_yaml(config_path)
    check_config_sections(cfg, config_path)

    proj_cfg  = cfg["project"]
    plant_cfg = cfg["plant"]
    sim_cfg   = cfg["simulation"]
    elem_cfg  = cfg["elements"]
    out_dir   = gcc.results_dir(cfg, "manifests")    # <paths.root>\Run Manifests

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
    reduced_p, p_factor = frt_reduced_p(sim_cfg)
    app.PrintPlain("Reduced P: " + (
        f"ON — all cases at {p_factor:g} x Pmax = {p_factor * Pmax:.2f} MW, "
        f"Q = +/-{Qmax:.2f} Mvar" if reduced_p
        else "off — P as set in the base case"))

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
    plants   = refs["plants"]
    plot_names = gcc.plot_label_names(cfg)
    record_vars = gcc.record_vars_frt(elem_cfg)
    Ext_grid = refs["Ext_grid"]
    SC       = refs["SC"]
    PFC      = refs["PFC"]
    base_case      = refs["base_case"]

    # The cases run with the base case's own RMS settings (apart from the stop
    # time): read them and warn if they do not suit the FRT cases.
    if base_case is not None:
        check_base_case_rms(app, base_case, study_cases, recovery_time_s)
    base_scenario  = refs["base_scenario"]
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
    # 7.8  What the script changes on each copied case, besides the events,
    #      recorded variables and plots:
    #        * Q setpoint. Written into the case's own scenario (a copy of the
    #          base case's scenario), never before that scenario is active, so
    #          the base case is never modified.
    #        * P setpoint — only when simulation.frt_reduced_p = 1 (all cases),
    #          into the same case scenario.
    #        * Simulation stop time (ComSim.tstop) from simulation.t_stop_s.
    # --------------------------------------------------------------------------
    def set_q_setpoint(qsetp):
        SC.qsetp = QSETP_SIGN * qsetp   # controller sign convention
        app.PrintPlain(f"    Q setpoint: SC.qsetp = {QSETP_SIGN * qsetp:.3f} Mvar")

    def set_p_setpoint(sc_cfg):
        """Reduced P for this case (frt_reduced_p); otherwise P is not touched."""
        if sc_cfg.get("p_reduced"):
            PFC.psetp = float(sc_cfg["psetp"])
            app.PrintPlain(f"    P setpoint: PFC.psetp = {sc_cfg['psetp']:.3f} MW "
                           f"(reduced)")

    def set_stop_time(t_stop):
        """Simulation stop time from simulation.t_stop_s (ComSim.tstop only)."""
        app.GetFromStudyCase("ComSim").tstop = float(t_stop)
        app.PrintPlain(f"    Stop time : {float(t_stop):g} s")

    # --------------------------------------------------------------------------
    # 7.9  Create all operational scenarios up front, each a copy of the
    #      base case's scenario (so its operational data is kept)
    # --------------------------------------------------------------------------
    opscen_objects = {}
    if not run_sim_only:
        app.PrintPlain(f"\nCreating {len(study_cases)} operational scenarios...")
        for sc_cfg in study_cases:
            name = sc_cfg["name"]
            existing = opscen_folder.GetContents(f"{name}.IntScenario")
            if existing:
                opscen_objects[name] = existing[0]
            elif base_scenario is not None:
                opscen_objects[name] = opscen_folder.AddCopy(base_scenario, name)
            else:
                opscen_objects[name] = opscen_folder.CreateObject("IntScenario", name)
        app.PrintPlain(f"  Done: {len(opscen_objects)} scenarios created"
                       + (f" (copies of '{base_scenario.loc_name}')."
                          if base_scenario is not None else "."))

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
                # B. Activate the case and link its own scenario (a copy of
                #    the base case's scenario, so all operational data is
                #    kept and the base case's scenario is never written to)
                # --------------------------------------------------------------
                study_case.Activate()
                opscen = opscen_objects.get(sc_name)
                if opscen:
                    opscen.Activate()

                # --------------------------------------------------------------
                # C. Fault events
                # --------------------------------------------------------------
                R_f, X_f = compute_fault_impedance(uret, usetp, nom_Z, ratio_R_X)
                app.PrintPlain(f"    Fault Z  : R={R_f:.5f}  X={X_f:.5f} Ohm")

                create_fault_event(app, POC_BB, t_fault, ft_int, R_f, X_f)  # apply
                create_fault_event(app, POC_BB, t_clear, I_SHC_CLEAR, R_f, X_f)  # clear

                # --------------------------------------------------------------
                # D. Record variables (added to the case's result object;
                #    no calculation is run and no setting is changed)
                # --------------------------------------------------------------
                elmRes = app.GetFromStudyCase("ComInc").p_resvar
                if elmRes is None:
                    app.PrintWarn("    No result object set in the case's "
                                  "ComInc — variables not recorded.")
                else:
                    add_recorded_results(elmRes, elm_objects, app,
                                         record_vars + POC_PLOT_VARS)

                # --------------------------------------------------------------
                # E. Plot pages
                # --------------------------------------------------------------
                setup_plot_pages(app, sc_cfg, recovery_time_s,
                                 POC_BB, POC_BRK, plants, plot_names)
                app.PrintPlain("    Plot pages : POC, "
                               + ", ".join(gu_page_names(len(plants)))
                               + " — configured.")

                # --------------------------------------------------------------
                # F. Q setpoint, then save the case scenario so the case is
                #    self-contained when opened manually
                # --------------------------------------------------------------
                set_stop_time(t_stop)
                set_p_setpoint(sc_cfg)
                set_q_setpoint(qsetp)
                if opscen:
                    try:
                        opscen.Save()
                    except Exception as e:
                        app.PrintWarn(f"    Could not save op scenario: {e}")

            else:
                # --------------------------------------------------------------
                # run_sim_only — activate the existing study case from the
                # run folder resolved in 7.6, then RE-APPLY the Q setpoint
                # for this case (it travels in sc_cfg, so Q0/Qmax/Qmin are
                # always right). Nothing else is changed.
                # --------------------------------------------------------------
                existing = sc_folder.GetContents(f"{sc_name}.IntCase")
                if not existing:
                    raise RuntimeError(
                        f"Study case '{sc_name}' not found in '{run_tag}'. "
                        f"Re-run with setup_only=True to recreate study cases."
                    )
                existing[0].Activate()
                set_stop_time(t_stop)
                set_p_setpoint(sc_cfg)
                set_q_setpoint(qsetp)

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

    if gcc.LABEL_ERRORS:
        app.PrintWarn(
            f"\nCurve legend labels could not be set ({len(gcc.LABEL_ERRORS)} "
            f"plot(s)), default PowerFactory labels are shown. First error: "
            f"{gcc.LABEL_ERRORS[0]}\nCheck gcc_common.CURVE_LABEL_ATTR "
            f"('{gcc.CURVE_LABEL_ATTR}') against the 'Label' column of a "
            f"plot's data series in this PowerFactory version.")

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
    #        Handled by gcc_export_raw_results.py (one folder per case,
    #        one CSV per plot page: the input of frt_check.py).
    # --------------------------------------------------------------------------
    if not setup_only:
        app.PrintPlain(
            "\nResults export: run gcc_export_raw_results.py, then frt_check.py."
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
            "script_version": "5.8",
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
        app.PrintPlain("  Export   : run gcc_export_raw_results.py")
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
