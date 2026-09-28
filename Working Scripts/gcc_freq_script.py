# ==============================================================================
# GCC Frequency Simulation Script
# ==============================================================================
# Creates study cases, operational scenarios, RMS simulation settings, and
# plot pages for FSM, LFSM-O, and LFSM-U frequency simulations — then runs
# them and writes a JSON run manifest (the evaluation tool's input).
#
# Reads the same project_config.yaml as the FRT script. Everything
# frequency-related — which simulation types apply, step sequences, timing,
# plot references — comes from the grid code file
# (requirements_type_2.active_power_frequency_response). To change them, edit
# the grid code YAML only; this script stays unchanged across projects.
#
# Per applicable simulation type (FSM / LFSMO / LFSMU), two study cases:
#   - <TYPE> Ramp  (frequency ramp — events come from an external .txt file
#                   already set up in the base case)
#   - <TYPE> Step  (frequency steps — EvtParam events on the ElmVac frequency
#                   source 'elements.freq_voltage_source', parameter F0Hz,
#                   named {TYPE}_Step_{freq_hz}Hz_t{time}s)
#
# Each study case gets three plot pages:
#   Plot 1 — Main Results       : frequency, voltage, P, Q (2x2)
#   Plot 2 — Active power       : frequency + P overlay + step reference lines
#   Plot 3 — Active power zoomed: as Plot 2 + one extra zoom reference line
#
# Cases always run sequentially (six long cases do not parallelise usefully
# and need the per-case convergence diagnostics).
# Results are exported afterwards by gcc_export_results.py (STUDY = "FREQ").
#
# Usage:
#   1. Edit project_config.yaml for your project
#   2. Set CONFIG_FILE below — a file, a folder containing
#      project_config(_update).yaml, or None for the one next to this script
#   3. Point a ComPython object at this file and run from PowerFactory
#
# Dependencies:
#   PyYAML         — "<PF Python>\python.exe" -m pip install pyyaml
#   gcc_common.py  — v2.0+, must sit in the SAME FOLDER as this script
#
# Author  : GCC Automation Team
# Version : 3.0
#
# CHANGELOG v2.1 -> v3.0
# ----------------------
#   * Shared plumbing (config/element lookup, run folders, case copy,
#     setpoints, simulation run, summary) now comes from gcc_common v2.0;
#     this script only holds what is frequency-specific.
#   * Hang protection (iopt_adapt=1 + dtgrd_max) is now set when the case is
#     CREATED, as in the FRT script, so cases run later by hand carry it too.
#   * Manifest records dtgrd_max_s (used by the exporter's completeness check)
#     and the correct script version.
# ==============================================================================

import sys
import os
import time as clock

# ------------------------------------------------------------------------------
# Make gcc_common.py importable (it sits next to this script)
# ------------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcc_common as gcc


SCRIPT_VERSION = "3.0"

# ------------------------------------------------------------------------------
# PATH TO PROJECT CONFIG — file, folder, or None (project_config.yaml next to
# this script)
# ------------------------------------------------------------------------------
CONFIG_FILE = r"C:\Projects\GCC Script Development\Codemasters\Working Scripts\project_config_update.yaml"
# CONFIG_FILE = None

# Name of the base study case to copy when 'elements.base_case' is not set.
BASE_CASE_NAME = "GCC_BaseCase"

# Maximum adaptive timestep [s] — solver hang protection (see
# gcc.enable_hang_protection). PF may also GROW the step up to this value in
# quiet periods, which speeds up the long (400+ s) runs; 0.1 s still resolves
# the multi-second droop/PPC response.
DTGRD_MAX_S = 0.1

RECORD_VARS = gcc.RECORD_VARS_FREQ

ELEMENTS_REQUIRED = ("poc_busbar", "poc_breaker", "external_grid",
                     "station_ctrl", "pfc_ctrl", "freq_voltage_source")

# Grid code YAML key -> simulation type name used in case/page/event names
TYPE_KEYS = {"lfsm_o": "LFSMO", "lfsm_u": "LFSMU", "fsm": "FSM"}


# ==============================================================================
# 1.  FREQUENCY SIMULATION CONFIG — read from the grid code YAML
# ==============================================================================

def get_freq_config(gc_cfg):
    """
    Read requirements_type_2.active_power_frequency_response and return
    (type_configs, t_start_s, t_stop_s, step_dur_s), where type_configs is
    keyed by type name ('LFSMO', 'LFSMU', 'FSM') for the applicable types:
        { 'step_events': [(time_s, freq_hz), ...], 'plot_x_refs_s': [...],
          'plot_zoom_ref_s': float, 'last_step_s': float }

    Raises RuntimeError if a required block is missing or inconsistent.
    """
    freq_block = (gc_cfg.get("requirements_type_2", {})
                        .get("active_power_frequency_response", {}))
    if not freq_block:
        raise RuntimeError(
            "Block 'requirements_type_2.active_power_frequency_response' "
            "not found in grid code YAML.")

    timing     = freq_block.get("simulation_timing", {})
    t_start_s  = float(timing.get("t_start_s",        0.0))
    t_stop_s   = float(timing.get("t_stop_s",       420.0))
    step_dur_s = float(timing.get("step_duration_s",  30.0))

    type_configs = {}
    for yaml_key, type_name in TYPE_KEYS.items():
        block = freq_block.get(yaml_key, {})
        if not block.get("applicable"):
            continue

        setup = block.get("simulation_setup")
        if setup is None:
            raise RuntimeError(
                f"'{yaml_key}.simulation_setup' block missing from grid code YAML.\n"
                "Add step_events, plot_x_refs_s, and plot_zoom_ref_s under "
                f"active_power_frequency_response.{yaml_key}.simulation_setup")
        if not setup.get("step_events"):
            raise RuntimeError(
                f"'{yaml_key}.simulation_setup.step_events' is empty "
                "in grid code YAML.")

        step_events = [(float(e["time_s"]), float(e["freq_hz"]))
                       for e in setup["step_events"]]
        last_step = max(t for t, _ in step_events)
        if last_step + step_dur_s > t_stop_s:
            raise RuntimeError(
                f"'{yaml_key}': last step at {last_step:.0f}s + step_duration "
                f"{step_dur_s:.0f}s exceeds t_stop_s ({t_stop_s:.0f}s). "
                f"Increase simulation_timing.t_stop_s.")

        type_configs[type_name] = {
            "step_events":     step_events,
            "plot_x_refs_s":   [float(t) for t in setup.get("plot_x_refs_s", [])],
            "plot_zoom_ref_s": float(setup.get("plot_zoom_ref_s", 0.0)),
            "last_step_s":     last_step,
        }

    return type_configs, t_start_s, t_stop_s, step_dur_s


def generate_study_cases(type_configs, t_start_s, t_stop_s, step_dur_s, plant):
    """
    A Ramp and a Step case per applicable type. Everything the evaluation
    tool needs travels in these dicts into the run manifest — it never has to
    parse case names.
    """
    cases = []
    for sim_type, tc in type_configs.items():
        for variant in ("Ramp", "Step"):
            cases.append({
                "name":            f"{sim_type} {variant}",
                "type":            sim_type,
                "variant":         variant,
                "t_start":         t_start_s,
                "t_stop":          t_stop_s,
                "step_duration_s": step_dur_s,
                "step_events":     tc["step_events"] if variant == "Step" else None,
                "psetp":           plant["pmax_mw"],
                "qsetp":           0.0,
                "usetp":           1.0,
            })
    return cases


# ==============================================================================
# 2.  CASE SETUP — RMS settings, step events, plots
# ==============================================================================

def configure_rms(comInc, comSim, sim_cfg, t_start_s, t_stop_s):
    """RMS settings for a balanced (positive-sequence) frequency simulation."""
    comInc.iopt_sim   = "rms"
    comInc.iopt_show  = 0
    comInc.iopt_adapt = 0
    comInc.dtgrd      = float(sim_cfg.get("integration_step_s", 0.01))
    comInc.tstart     = t_start_s
    comInc.iopt_lt    = int(sim_cfg.get("a_stable_integration", 1))
    comInc.iopt_net   = "sym"
    comSim.tstop      = t_stop_s


def create_step_events(app, sim_type, freq_voltage_source, step_events):
    """
    Create one EvtParam per step on the ElmVac frequency source, setting its
    F0Hz parameter at the step time. Events matching THIS script's naming
    convention (*_Step_*.EvtParam) are removed first so re-runs never
    duplicate them; other EvtParam events in the base case are untouched.
    """
    evt_folder = app.GetFromStudyCase("Simulation Events/Fault.IntEvt")
    if evt_folder is None:
        app.PrintWarn("    Could not access simulation events folder — "
                      "events not created.")
        return

    for old_evt in evt_folder.GetContents("*_Step_*.EvtParam") or []:
        try:
            old_evt.Delete()
        except Exception:
            pass

    app.PrintPlain(f"    Creating {len(step_events)} step event(s) for {sim_type}...")
    for time_s, freq_hz in step_events:
        evt_name     = f"{sim_type}_Step_{freq_hz:.1f}Hz_t{int(time_s)}s"
        evt          = evt_folder.CreateObject("EvtParam", evt_name)
        evt.p_target = freq_voltage_source
        evt.time     = float(time_s)
        evt.variable = "F0Hz"
        evt.value    = str(freq_hz)
        app.PrintPlain(f"      {evt_name}  ->  F0Hz = {freq_hz} Hz  @ t = {time_s} s")
    app.PrintPlain("    Step events created successfully.")


def setup_freq_plot_pages(app, case, tc, elms):
    """
    The three standard frequency plot pages for the active study case.
    x-range — Step: [t_start, min(t_stop, last_step + step_duration)];
              Ramp: [t_start, t_stop] (its events live in an external file).
    """
    x_min = case["t_start"]
    x_max = case["t_stop"]
    if case["variant"] == "Step":
        x_max = min(case["t_stop"], tc["last_step_s"] + case["step_duration_s"])

    poc, brk = elms["poc_busbar"], elms["poc_breaker"]
    freq_curve = (poc, "m:fehz",      gcc.C_FREQ)
    p_curve    = (brk, "m:Psum:bus1", gcc.C_P)
    prefix     = case["type"]

    # PAGE 1 — Main Results (2x2)
    page1_name = f"{prefix} Plot 1 - Main Results"
    page1 = gcc.new_plot_page(app, page1_name)
    graphs = [
        gcc.make_graph(page1, "Curve plot",    [freq_curve], row=0, col=0),
        gcc.make_graph(page1, "Curve plot(1)", [(poc, "m:u1", gcc.C_VOLT)],
                       row=0, col=1),
        gcc.make_graph(page1, "Curve plot(2)", [p_curve], row=1, col=0),
        gcc.make_graph(page1, "Curve plot(3)", [(brk, "m:Qsum:bus1", gcc.C_Q)],
                       row=1, col=1),
    ]
    for g in graphs:
        gcc.set_xrange(g, x_min, x_max, app)
    gcc.set_2x2_layout(page1)
    app.Rebuild(2)

    # PAGE 2 — Active power with step reference lines
    page2_name = f"{prefix} Plot 2 - Active power with labels"
    page2 = gcc.new_plot_page(app, page2_name)
    plot2 = gcc.make_graph(page2, "Curve plot(2)", [freq_curve, p_curve],
                           x_refs=tc["plot_x_refs_s"])
    gcc.set_xrange(plot2, x_min, x_max, app)
    plot2.DoAutoScaleY()
    app.Rebuild(2)

    # PAGE 3 — Active power zoomed (auto-scaled, extra zoom reference line)
    page3_name = f"{prefix} Plot 3 - Active power zoomed"
    page3 = gcc.new_plot_page(app, page3_name)
    plot3 = gcc.make_graph(page3, "Curve plot(2)", [freq_curve, p_curve],
                           x_refs=list(tc["plot_x_refs_s"]) + [tc["plot_zoom_ref_s"]])
    plot3.DoAutoScaleX()
    plot3.DoAutoScaleY()
    app.Rebuild(2)

    app.PrintPlain(f"    Plot pages  : '{page1_name}', '{page2_name}', "
                   f"'{page3_name}' — configured.")


def setup_case(app, case, ctx):
    """Create and configure one study case (setup path)."""
    tc = ctx["type_configs"][case["type"]]
    study_case = gcc.copy_base_case(app, ctx["sc_folder"], ctx["base_case"],
                                    case["name"])
    opscen = ctx["opscens"].get(case["name"])
    if opscen is None:
        app.PrintWarn(f"    Op scenario '{case['name']}' not found — skipping link.")
    gcc.activate_case(app, study_case, ctx["elms"], ctx["netdat"], case, opscen)

    comInc = app.GetFromStudyCase("ComInc")
    gcc.configure_loadflow(app.GetFromStudyCase("ComLDF"))
    configure_rms(comInc, app.GetFromStudyCase("ComSim"), ctx["sim_cfg"],
                  case["t_start"], case["t_stop"])

    if case["variant"] == "Step":
        create_step_events(app, case["type"], ctx["elms"]["freq_voltage_source"],
                           tc["step_events"])
    else:
        app.PrintPlain("    Ramp variant — events loaded from external .txt file.")

    comInc.Execute()
    gcc.add_recorded_results(app, comInc.p_resvar, ctx["elms"], RECORD_VARS)

    setup_freq_plot_pages(app, case, tc, ctx["elms"])


# ==============================================================================
# 3.  MAIN
# ==============================================================================

def _run(app):
    gcc.print_banner(app, f"GCC Frequency Simulation Script v{SCRIPT_VERSION} — starting")
    t0 = clock.perf_counter()

    # --- Config and grid code -------------------------------------------------
    config_path = gcc.resolve_config_path(CONFIG_FILE, __file__)
    cfg, gc_cfg, gc_path = gcc.load_project(config_path, app)
    proj_cfg, sim_cfg, elem_cfg = cfg["project"], cfg["simulation"], cfg["elements"]
    out_dir = cfg["results"]["output_dir"]
    gc_meta = gc_cfg.get("meta", {})
    gc_name = (f"{gc_meta.get('grid_code', {}).get('name', '?')}  "
               f"Type {gc_meta.get('module_type', '?')}")

    plant = gcc.plant_values(cfg["plant"])
    app.PrintPlain(f"\nProject  : {proj_cfg['name']}  |  {proj_cfg.get('number', '')}")
    app.PrintPlain(f"Grid code: {gc_name}")
    app.PrintPlain(f"Pmax={plant['pmax_mw']:.1f} MW  U_POC={plant['u_poc_kv']:.1f} kV  "
                   f"Qmax=+/-{plant['qmax_mvar']:.2f} Mvar")

    setup_only   = bool(sim_cfg.get("setup_only",   True))
    run_sim_only = bool(sim_cfg.get("run_sim_only", False))
    app.PrintPlain(f"Flags    : setup_only={setup_only}  run_sim_only={run_sim_only}")

    # --- Study cases from the grid code ---------------------------------------
    type_configs, t_start_s, t_stop_s, step_dur_s = get_freq_config(gc_cfg)
    if not type_configs:
        app.PrintError(
            "No applicable frequency simulation types found in the grid code.\n"
            "Check active_power_frequency_response under requirements_type_2\n"
            f"in: {gc_path}")
        return
    study_cases = generate_study_cases(type_configs, t_start_s, t_stop_s,
                                       step_dur_s, plant)

    app.PrintPlain(f"\nApplicable types : {list(type_configs)}")
    app.PrintPlain(f"Simulation timing: t_start={t_start_s}s  t_stop={t_stop_s}s  "
                   f"step_duration={step_dur_s}s")
    app.PrintPlain(f"Study cases      : {[sc['name'] for sc in study_cases]}")

    # --- Preflight: nothing is created in the project before this passes ------
    app.PrintPlain("\nPreflight checks...")
    elms = gcc.find_elements(app, elem_cfg, ELEMENTS_REQUIRED)
    base_case = (None if run_sim_only
                 else gcc.find_base_case(app, elem_cfg, BASE_CASE_NAME))
    gcc.check_output_dir(out_dir)
    app.PrintPlain(f"  [ok] Output folder    : {out_dir}")

    # --- Run folder, controllers, operational scenarios -----------------------
    run_tag, sc_folder, opscen_folder = gcc.open_run_folder(app, "FREQ", run_sim_only)
    gcc.set_controller_modes(elms)

    opscens = {}
    if not run_sim_only:
        app.PrintPlain(f"\nCreating {len(study_cases)} operational scenarios...")
        for sc in study_cases:
            # One sub-folder per simulation type keeps the scenario tree tidy
            type_folder = gcc.get_or_create(opscen_folder, sc["type"], "IntFolder")
            opscens[sc["name"]] = gcc.get_or_create(type_folder, sc["name"],
                                                    "IntScenario")
            app.PrintPlain(f"  [ok] '{sc['name']}'")

    ctx = {"elms": elms, "base_case": base_case, "sc_folder": sc_folder,
           "opscens": opscens, "netdat": gcc.get_folder(app, "netdat"),
           "sim_cfg": sim_cfg, "type_configs": type_configs}

    # --- Main loop: one case at a time; a failure never stops the batch -------
    app.PrintPlain(f"\nProcessing {len(study_cases)} study case(s)...")
    failed = {}   # {case_name: reason}

    for case in study_cases:
        app.PrintPlain(f"\n  [{case['name']}]  type={case['type']}  "
                       f"variant={case['variant']}")
        try:
            if run_sim_only:
                gcc.activate_case(app, gcc.find_run_case(sc_folder, case["name"]),
                                  elms, ctx["netdat"], case)
            else:
                setup_case(app, case, ctx)

            gcc.enable_hang_protection(app.GetFromStudyCase("ComInc"), DTGRD_MAX_S)
            if not setup_only:
                gcc.run_simulation(app)
        except Exception as exc:
            gcc.record_failure(app, failed, case["name"], exc)

    if not setup_only:
        app.PrintPlain("\nResults export: run gcc_export_results.py (STUDY='FREQ') "
                       "to write one CSV per case.")

    # --- Run manifest ---------------------------------------------------------
    manifest_path = gcc.write_run_manifest(
        out_dir, run_tag, proj_cfg, plant, gc_path, gc_meta, study_cases, failed,
        extra={
            "setup_only":     setup_only,
            "run_sim_only":   run_sim_only,
            "dtgrd_max_s":    DTGRD_MAX_S,
            "script_version": SCRIPT_VERSION,
            "simulation_timing": {
                "t_start_s":       t_start_s,
                "t_stop_s":        t_stop_s,
                "step_duration_s": step_dur_s,
            },
        })
    app.PrintPlain(f"\nRun manifest : {manifest_path}")

    # --- Summary --------------------------------------------------------------
    header = [f"Run       : {run_tag}",
              f"Grid code : {gc_name}",
              f"Types     : {list(type_configs)}",
              f"Runtime   : {round(clock.perf_counter() - t0, 2)}s"]
    footer = [f"\n  Manifest : {manifest_path}"]
    if not setup_only:
        footer.append("  Export   : run gcc_export_results.py (STUDY='FREQ')")
    gcc.print_summary(app, "GCC FREQUENCY SIMULATION SUMMARY", header,
                      len(study_cases), failed, footer)


def main():
    gcc.run_script("GCC Frequency script", _run)


if __name__ == "__main__":
    main()
