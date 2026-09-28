# ==============================================================================
# GCC Frequency Simulation Script
# ==============================================================================
# Creates study cases, operational scenarios, RMS simulation settings, and
# plot pages for FSM, LFSM-O, and LFSM-U frequency simulations — then runs
# them and exports results to CSV + a JSON run manifest.
#
# Reads the same project_config.yaml used by the FRT script.
# Grid code requirements (active_power_frequency_response) are read from the
# grid code file to determine which simulation types are applicable AND to
# drive all simulation setup — step sequences, timing, and plot references.
#
# NO frequency requirements are hardcoded in this script.
# To change step sequences, timing, or zoom references: edit the grid code
# YAML file only. This script stays unchanged across projects and grid codes.
#
# Per simulation type (FSM / LFSMO / LFSMU), two study cases are created:
#   - <TYPE> Ramp  (frequency ramp test — events loaded from external .txt file)
#   - <TYPE> Step  (frequency step test — events created automatically by script)
#
# Step events are created as EvtParam objects on the ElmVac frequency voltage
# source (elem_cfg['freq_voltage_source']), targeting the 'F0Hz' parameter.
# Event naming convention: {TYPE}_Step_{freq_hz}Hz_t{time}s
#   e.g. LFSMO_Step_50.2Hz_t30s
#
# Each study case gets three plot pages:
#   Plot 1 — Main Results       : 4 graphs (frequency, voltage, P, Q)
#   Plot 2 — Active power       : frequency + P overlay + X ref lines
#   Plot 3 — Active power zoomed: same as Plot 2 + one extra zoomed ref line
#
# Usage:
#   1. Edit project_config.yaml for your project
#   2. Set CONFIG_FILE below — or leave it as None to use the
#      project_config.yaml sitting NEXT TO this script
#   3. Point a ComPython object at this file and run from PowerFactory
#
# Dependencies:
#   PyYAML         — "<PF Python>\python.exe" -m pip install pyyaml
#   gcc_common.py  — must sit in the SAME FOLDER as this script
#
# Author  : GCC Automation Team
# Version : 2.1
#
# CHANGELOG v2.0 -> v2.1
# ----------------------
#   * RECORD_VARS_FREQ now imported from gcc_common — single authoritative
#     definition shared with the exporter.
#   * REMOVED: in-script CSV export. Results export is now exclusively the
#     job of gcc_export_results.py with STUDY = "FREQ" (one CSV per case +
#     export_manifest.json). Requires gcc_common v1.1+.
#   * CONFIG_FILE may now point at a FOLDER — the script looks for
#     project_config.yaml / project_config_update.yaml inside it.
#
# CHANGELOG v1.2 -> v2.0
# ----------------------
#   * Shared plumbing moved to gcc_common.py (single source for both scripts)
#   * NEW: all plotted variables are now explicitly RECORDED via
#     RECORD_VARS_FREQ. v1.2 recorded only P/Q — m:fehz and m:u1 were plotted
#     but never added to the result file, so frequency/voltage curves came up
#     empty unless the base case happened to record them already. This also
#     makes the CSV export possible.
#   * NEW: CSV export (FindColumn-based, via gcc_common) — the input for the
#     compliance evaluation tool
#   * NEW: JSON run manifest with per-case type / variant / step sequence /
#     timing / setpoints / status — the evaluator reads frequency sequences
#     from here, never from case names
#   * Convergence handling brought to FRT-script parity: three failure layers
#     (ComInc error, ComSim error, b:warnA fatal divergence) + iopt_adapt=1
#     with a dtgrd_max cap as hang protection; b:warnB/C reported as warnings
#   * Per-case try/except — one bad case can no longer kill the whole run;
#     failures recorded as {case_name: reason} for the OEM report
#   * run_sim_only: no longer creates a fresh (empty) run folder every time;
#     finds the latest FREQ_* folder instead (parses old DD-MM-YYYY names
#     too) and scopes case lookup to that folder — never recursively from
#     the study-case root, so a same-named case from an older run can never
#     be picked up
#   * run_sim_only: pre-fault setpoints are RE-APPLIED per case (v1.2 relied
#     on op scenarios that were never saved)
#   * Op scenario is activated BEFORE setpoints are written, then SAVED —
#     cases are self-contained when opened manually
#   * Preflight: config, elements, base case, and output dir all verified
#     BEFORE any run folder is created (no more empty FREQ_* folders left
#     behind on failure)
#   * Plot x-ranges derived from the YAML step sequence:
#     x_max = min(t_stop, last_step_time + step_duration). v1.2 hardcoded
#     0-270 s, which clipped the FSM sequence (last step at 300 s).
#     Ramp variants use the full [t_start, t_stop] window since their events
#     live in an external file.
#   * Step-event cleanup is scoped to this script's own naming convention
#     (*_Step_*.EvtParam) — deliberate EvtParam events in the base case
#     survive re-runs
#   * UI is guaranteed unfrozen on any error (gcc_common.pf_session)
#   * Run folders use ISO timestamps (FREQ_YYYY-MM-DD_HH-MM-SS)
# ==============================================================================

import sys
import os
import time as clock

# ------------------------------------------------------------------------------
# Make gcc_common.py importable (it sits next to this script)
# ------------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcc_common as gcc

import powerfactory


# ------------------------------------------------------------------------------
# PATH TO PROJECT CONFIG — same file used by the FRT script.
#   Set an absolute path here, OR leave as None to use the project_config.yaml
#   located in the same folder as this script.
# ------------------------------------------------------------------------------
CONFIG_FILE = r"C:\Projects\GCC Script Development\Codemasters\Working Scripts\project_config_update.yaml"
# CONFIG_FILE = None

# Name of the base study case to copy when 'elements.base_case' is not set.
BASE_CASE_NAME = "GCC_BaseCase"

# Maximum adaptive timestep [s] — hang protection. With iopt_adapt=1 PF may
# also GROW the step up to this value in quiet periods, which speeds up the
# long (400+ s) frequency runs considerably. 0.1 s still resolves the
# multi-second droop/PPC response with ample margin.
# NOTE: this makes the result time base NON-UNIFORM — the evaluation tool
# must interpolate on time, never index by row.
DTGRD_MAX_S = 0.1


# ==============================================================================
# 1.  RESULT VARIABLE RECORDING
#     Every variable that is plotted OR needed by the evaluation tool is
#     recorded explicitly — never assume the base case already records it.
#     (element_key matches keys in cfg["elements"])
# ==============================================================================

# Single authoritative definition lives in gcc_common.
RECORD_VARS_FREQ = gcc.RECORD_VARS_FREQ


def add_recorded_results(elmRes, elm_objects, app):
    """Add all RECORD_VARS_FREQ variables to the active case's ElmRes."""
    for elm_key, variable, _label in RECORD_VARS_FREQ:
        elm = elm_objects.get(elm_key)
        if elm is None:
            app.PrintWarn(
                f"  Record: element '{elm_key}' not found — skipping '{variable}'"
            )
            continue
        elmRes.AddVariable(elm, variable)


# ==============================================================================
# 2.  READ FREQUENCY SIMULATION CONFIG FROM GRID CODE YAML
# ==============================================================================

# Internal mapping: grid code YAML key -> script type string
_TYPE_KEY_MAP = {
    "lfsm_o": "LFSMO",
    "lfsm_u": "LFSMU",
    "fsm":    "FSM",
}


def get_freq_config(gc_cfg):
    """
    Read the active_power_frequency_response block from the grid code YAML and
    return:
      type_configs  — dict keyed by type string ('LFSMO', 'LFSMU', 'FSM'):
        {
          'step_events'    : [(time_s, freq_hz), ...],
          'plot_x_refs_s'  : [float, ...],
          'plot_zoom_ref_s': float,
        }
      t_start_s     — simulation start time [s]
      t_stop_s      — simulation stop time [s]
      step_dur_s    — duration of each step [s]

    Raises RuntimeError if a required block is missing or malformed.
    """
    freq_block = (gc_cfg
                  .get("requirements_type_2", {})
                  .get("active_power_frequency_response", {}))

    if not freq_block:
        raise RuntimeError(
            "Block 'requirements_type_2.active_power_frequency_response' "
            "not found in grid code YAML."
        )

    # Shared simulation timing
    timing     = freq_block.get("simulation_timing", {})
    t_start_s  = float(timing.get("t_start_s",       0.0))
    t_stop_s   = float(timing.get("t_stop_s",       420.0))
    step_dur_s = float(timing.get("step_duration_s",  30.0))

    type_configs = {}

    for yaml_key, type_str in _TYPE_KEY_MAP.items():
        block = freq_block.get(yaml_key, {})
        if not block.get("applicable"):
            continue

        sim_setup = block.get("simulation_setup")
        if sim_setup is None:
            raise RuntimeError(
                f"'{yaml_key}.simulation_setup' block missing from grid code YAML.\n"
                "Add step_events, plot_x_refs_s, and plot_zoom_ref_s under "
                f"active_power_frequency_response.{yaml_key}.simulation_setup"
            )

        raw_events = sim_setup.get("step_events", [])
        if not raw_events:
            raise RuntimeError(
                f"'{yaml_key}.simulation_setup.step_events' is empty "
                "in grid code YAML."
            )

        step_events  = [(float(e["time_s"]), float(e["freq_hz"])) for e in raw_events]
        plot_x_refs  = [float(t) for t in sim_setup.get("plot_x_refs_s", [])]
        plot_zoom    = float(sim_setup.get("plot_zoom_ref_s", 0.0))

        # Sanity check: t_stop must cover the last step + its hold duration
        last_step = max(t for t, _ in step_events)
        if last_step + step_dur_s > t_stop_s:
            raise RuntimeError(
                f"'{yaml_key}': last step at {last_step:.0f}s + "
                f"step_duration {step_dur_s:.0f}s exceeds t_stop_s "
                f"({t_stop_s:.0f}s). Increase simulation_timing.t_stop_s."
            )

        type_configs[type_str] = {
            "step_events":     step_events,
            "plot_x_refs_s":   plot_x_refs,
            "plot_zoom_ref_s": plot_zoom,
            "last_step_s":     last_step,
        }

    return type_configs, t_start_s, t_stop_s, step_dur_s


# ==============================================================================
# 3.  RMS SIMULATION CONFIGURATION  (frequency-specific — stays in this script)
# ==============================================================================

def configure_rms(comInc, comSim, sim_cfg, t_start_s, t_stop_s):
    """
    Configure ComInc and ComSim for frequency RMS simulation (balanced).
    t_start_s and t_stop_s are passed in from the YAML simulation_timing block.
    """
    comInc.iopt_sim   = "rms"
    comInc.iopt_show  = 0
    comInc.iopt_adapt = 0
    comInc.dtgrd      = float(sim_cfg.get("integration_step_s", 0.01))
    comInc.tstart     = t_start_s
    comInc.iopt_lt    = int(sim_cfg.get("a_stable_integration", 1))
    comInc.iopt_net   = "sym"    # balanced — frequency studies use positive sequence
    comSim.tstop      = t_stop_s


# ==============================================================================
# 4.  STEP EVENT CREATION
# ==============================================================================

def create_step_events(app, sim_type, freq_voltage_source, step_events):
    """
    Create EvtParam events on the ElmVac frequency voltage source for a
    Step study case. Each event sets the 'F0Hz' parameter to the target
    frequency at the specified time.

    Naming convention: {TYPE}_Step_{freq_hz}Hz_t{time}s
      e.g. LFSMO_Step_50.2Hz_t30s

    Existing events matching THIS SCRIPT'S naming convention
    (*_Step_*.EvtParam) are cleared first to avoid duplicates on re-runs.
    EvtParam events with other names (e.g. placed deliberately in the base
    case) are left untouched.
    """
    evt_folder = app.GetFromStudyCase("Simulation Events/Fault.IntEvt")
    if evt_folder is None:
        app.PrintWarn("    Could not access simulation events folder — events not created.")
        return

    # Clear only OUR events — scoped to the script's naming convention
    existing_events = evt_folder.GetContents("*_Step_*.EvtParam") or []
    for old_evt in existing_events:
        try:
            old_evt.Delete()
        except Exception:
            pass

    app.PrintPlain(f"    Creating {len(step_events)} step event(s) for {sim_type}...")

    for time_s, freq_hz in step_events:
        evt_name     = f"{sim_type}_Step_{freq_hz:.1f}Hz_t{int(time_s)}s"
        evt          = evt_folder.CreateObject("EvtParam", evt_name)
        evt.p_target = freq_voltage_source   # target element: ElmVac
        evt.time     = float(time_s)         # event time [s]
        evt.variable = "F0Hz"                # frequency parameter on ElmVac
        evt.value    = str(freq_hz)          # target frequency value [Hz]

        app.PrintPlain(f"      {evt_name}  ->  F0Hz = {freq_hz} Hz  @ t = {time_s} s")

    app.PrintPlain("    Step events created successfully.")


# ==============================================================================
# 5.  PLOT PAGE SETUP
#     x-range for Step variants is derived from the YAML step sequence:
#       [t_start, min(t_stop, last_step + step_duration)]
#     Ramp variants use the full [t_start, t_stop] window (their events live
#     in an external file, so the sequence end is unknown to this script).
# ==============================================================================

def setup_freq_plot_pages(app, sim_type, variant, POC_BB, POC_BRK,
                          tc, t_start_s, t_stop_s, step_dur_s):
    """Create the three standard frequency plot pages for the active study case."""
    desktop = app.GetGraphicsBoard()
    comInc  = app.GetFromStudyCase("ComInc")
    elmRes  = comInc.p_resvar

    plot_x_refs_s   = tc["plot_x_refs_s"]
    plot_zoom_ref_s = tc["plot_zoom_ref_s"]

    if variant == "Step":
        x_min = t_start_s
        x_max = min(t_stop_s, tc["last_step_s"] + step_dur_s)
    else:
        x_min = t_start_s
        x_max = t_stop_s

    p_elm, p_var = POC_BRK, "m:Psum:bus1"
    q_elm, q_var = POC_BRK, "m:Qsum:bus1"

    prefix = sim_type   # 'FSM', 'LFSMO', or 'LFSMU'

    # ------------------------------------------------------------------
    # PAGE 1 — Main Results (4 graphs in 2x2 layout)
    # ------------------------------------------------------------------
    page1_name = f"{prefix} Plot 1 - Main Results"
    page1      = desktop.GetPage(page1_name, 1, "GrpPage")
    page1.SetResults(elmRes)

    g1 = gcc.make_graph(page1, "Curve plot",    [(POC_BB, "m:fehz", gcc.C_FREQ)],
                        row=0, col=0)
    g2 = gcc.make_graph(page1, "Curve plot(1)", [(POC_BB, "m:u1",   gcc.C_VOLT)],
                        row=0, col=1)
    g3 = gcc.make_graph(page1, "Curve plot(2)", [(p_elm,   p_var,   gcc.C_P)],
                        row=1, col=0)
    g4 = gcc.make_graph(page1, "Curve plot(3)", [(q_elm,   q_var,   gcc.C_Q)],
                        row=1, col=1)

    for _g in (g1, g2, g3, g4):
        gcc.set_xrange(_g, x_min, x_max, app)

    try:
        page1.SetLayoutMode(2)
        page1.numLayoutColumns = 2
    except Exception:
        pass

    app.Rebuild(2)

    # ------------------------------------------------------------------
    # PAGE 2 — Active power with labels
    # ------------------------------------------------------------------
    page2_name = f"{prefix} Plot 2 - Active power with labels"
    page2      = desktop.GetPage(page2_name, 1, "GrpPage")
    page2.SetResults(elmRes)

    plot2 = gcc.make_graph(page2, "Curve plot(2)", [
        (POC_BB, "m:fehz", gcc.C_FREQ),
        (p_elm,   p_var,   gcc.C_P),
    ], x_refs=plot_x_refs_s)

    gcc.set_xrange(plot2, x_min, x_max, app)
    plot2.DoAutoScaleY()
    app.Rebuild(2)

    # ------------------------------------------------------------------
    # PAGE 3 — Active power zoomed
    # ------------------------------------------------------------------
    page3_name = f"{prefix} Plot 3 - Active power zoomed"
    page3      = desktop.GetPage(page3_name, 1, "GrpPage")
    page3.SetResults(elmRes)

    plot3 = gcc.make_graph(page3, "Curve plot(2)", [
        (POC_BB, "m:fehz", gcc.C_FREQ),
        (p_elm,   p_var,   gcc.C_P),
    ], x_refs=list(plot_x_refs_s) + [plot_zoom_ref_s])

    plot3.DoAutoScaleX()
    plot3.DoAutoScaleY()
    app.Rebuild(2)

    app.PrintPlain(
        f"    Plot pages  : '{page1_name}', '{page2_name}', '{page3_name}' — configured."
    )


# ==============================================================================
# 6.  PREFLIGHT
#     Everything that can fail is checked HERE, before any run folder or
#     scenario is created. If preflight raises, the PF project is untouched.
# ==============================================================================

def preflight(app, sim_cfg, elem_cfg, out_dir, run_sim_only):
    """
    Verify config, model elements, base case, and output folder.

    Returns (elm_objects, refs) where:
      elm_objects — {elm_key: PF object} for result recording / CSV export
      refs        — dict of the individually named objects the main loop uses
    """
    app.PrintPlain("\nPreflight checks...")

    # --- 6.1 Required config keys ---------------------------------------------
    for key in ("poc_busbar", "poc_breaker", "external_grid",
                "station_ctrl", "pfc_ctrl", "freq_voltage_source"):
        if key not in elem_cfg:
            raise RuntimeError(f"Missing 'elements.{key}' in project config.")
    app.PrintPlain("  [ok] Config keys")

    # --- 6.2 Model elements ----------------------------------------------------
    POC_BB           = gcc.find_element(app, "ElmTerm",    elem_cfg["poc_busbar"])
    POC_BRK          = gcc.find_element(app, "ElmCoup",    elem_cfg["poc_breaker"])
    Ext_grid         = gcc.find_element(app, "ElmXnet",    elem_cfg["external_grid"])
    SC               = gcc.find_element(app, "ElmStactrl", elem_cfg["station_ctrl"])
    PFC              = gcc.find_element(app, "ElmSecctrl", elem_cfg["pfc_ctrl"])
    freq_volt_source = gcc.find_element(app, "ElmVac",     elem_cfg["freq_voltage_source"])

    app.PrintPlain(f"  [ok] POC busbar       : {POC_BB.loc_name}")
    app.PrintPlain(f"  [ok] POC breaker      : {POC_BRK.loc_name}")
    app.PrintPlain(f"  [ok] Ext grid         : {Ext_grid.loc_name}")
    app.PrintPlain(f"  [ok] Station ctrl     : {SC.loc_name}")
    app.PrintPlain(f"  [ok] PFC ctrl         : {PFC.loc_name}")
    app.PrintPlain(f"  [ok] Freq volt source : {freq_volt_source.loc_name}")

    # --- 6.3 Base case (only needed when creating cases) -----------------------
    sc_folder_root = gcc.get_folder(app, "study")
    base_case = None
    if not run_sim_only:
        base_case_name = elem_cfg.get("base_case", BASE_CASE_NAME)
        base_matches   = sc_folder_root.GetContents(f"{base_case_name}.IntCase")
        if not base_matches:
            raise RuntimeError(
                f"Base case '{base_case_name}' not found in Study Cases folder.\n"
                "Check 'elements.base_case' in project_config.yaml."
            )
        base_case = base_matches[0]
        app.PrintPlain(f"  [ok] Base case        : {base_case.loc_name}")

    # --- 6.4 Output folder writable --------------------------------------------
    try:
        os.makedirs(out_dir, exist_ok=True)
        probe = os.path.join(out_dir, ".gcc_write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        raise RuntimeError(f"Output folder is not writable: {out_dir}\n{e}")
    app.PrintPlain(f"  [ok] Output folder    : {out_dir}")

    elm_objects = {
        "poc_busbar":  POC_BB,
        "poc_breaker": POC_BRK,
    }
    refs = {
        "POC_BB": POC_BB, "POC_BRK": POC_BRK,
        "Ext_grid": Ext_grid, "SC": SC, "PFC": PFC,
        "freq_volt_source": freq_volt_source,
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
    app.PrintPlain("  GCC Frequency Simulation Script v2.0 — starting")
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
    # 7.2  Plant values
    # --------------------------------------------------------------------------
    Pmax  = float(plant_cfg["pmax_mw"])
    U_POC = float(plant_cfg["poc_voltage_kv"])
    Qfac  = float(plant_cfg.get("q_factor", 0.33))
    Qmax  =  Qfac * Pmax
    Qmin  = -Qfac * Pmax

    app.PrintPlain(f"\nProject  : {proj_cfg['name']}  |  {proj_cfg.get('number', '')}")
    app.PrintPlain(f"Grid code: {gc_meta.get('grid_code', {}).get('name', '?')}  "
                   f"Type {gc_meta.get('module_type', '?')}")
    app.PrintPlain(f"Pmax={Pmax:.1f} MW  U_POC={U_POC:.1f} kV  Qmax=+/-{Qmax:.2f} Mvar")

    # --------------------------------------------------------------------------
    # 7.3  Flags
    # --------------------------------------------------------------------------
    setup_only   = bool(sim_cfg.get("setup_only",   True))
    run_sim_only = bool(sim_cfg.get("run_sim_only", False))
    app.PrintPlain(f"Flags    : setup_only={setup_only}  run_sim_only={run_sim_only}")

    # --------------------------------------------------------------------------
    # 7.4  Read all frequency simulation config from grid code YAML
    #       Nothing frequency-related is hardcoded in this script.
    # --------------------------------------------------------------------------
    type_configs, t_start_s, t_stop_s, step_dur_s = get_freq_config(gc_cfg)

    applicable = list(type_configs.keys())   # e.g. ['LFSMO', 'LFSMU', 'FSM']
    if not applicable:
        app.PrintError(
            "No applicable frequency simulation types found in the grid code.\n"
            "Check active_power_frequency_response under requirements_type_2\n"
            f"in: {gc_path}"
        )
        return

    app.PrintPlain(f"\nApplicable types : {applicable}")
    app.PrintPlain(f"Simulation timing: t_start={t_start_s}s  t_stop={t_stop_s}s  "
                   f"step_duration={step_dur_s}s")

    # Each applicable type gets two study cases: Ramp and Step.
    # Everything the evaluation tool needs travels in these dicts and lands
    # in the run manifest — it never has to parse case-name strings.
    study_cases = []
    for sim_type in applicable:
        tc = type_configs[sim_type]
        for variant in ("Ramp", "Step"):
            study_cases.append({
                "name":        f"{sim_type} {variant}",
                "type":        sim_type,
                "variant":     variant,
                "t_start":     t_start_s,
                "t_stop":      t_stop_s,
                "step_duration_s": step_dur_s,
                "step_events": (tc["step_events"] if variant == "Step" else None),
                "psetp":       Pmax,
                "qsetp":       0.0,
                "usetp":       1.0,
            })

    app.PrintPlain(f"Study cases      : {[sc['name'] for sc in study_cases]}")

    # --------------------------------------------------------------------------
    # 7.5  PREFLIGHT — before anything is created in the project
    # --------------------------------------------------------------------------
    elm_objects, refs = preflight(app, sim_cfg, elem_cfg, out_dir, run_sim_only)
    POC_BB           = refs["POC_BB"]
    POC_BRK          = refs["POC_BRK"]
    Ext_grid         = refs["Ext_grid"]
    SC               = refs["SC"]
    PFC              = refs["PFC"]
    freq_volt_source = refs["freq_volt_source"]
    base_case        = refs["base_case"]
    sc_folder_root   = refs["sc_folder_root"]

    opscen_folder_root = gcc.get_folder(app, "scen")
    netdat_folder      = gcc.get_folder(app, "netdat")

    # --------------------------------------------------------------------------
    # 7.6  Run folder — create new (setup) or find latest (run_sim_only)
    # --------------------------------------------------------------------------
    if run_sim_only:
        sc_folder = gcc.find_latest_run_folder(sc_folder_root, "FREQ")
        if sc_folder is None:
            app.PrintError(
                "run_sim_only=True but no FREQ_* run folder found in Study Cases.\n"
                "Run the script with setup_only=True first to create study cases."
            )
            return
        run_tag = sc_folder.loc_name
        app.PrintPlain(f"\nRun folder : '{run_tag}'  [reusing existing — "
                       f"latest by parsed timestamp]")
        opscen_folder = None
    else:
        run_tag       = gcc.make_run_tag("FREQ")
        sc_folder     = sc_folder_root.CreateObject("IntFolder", run_tag)
        opscen_folder = opscen_folder_root.CreateObject("IntFolder", run_tag)
        app.PrintPlain(f"\nRun folder : '{run_tag}'")

    # --------------------------------------------------------------------------
    # 7.7  Ensure controllers are in service and in correct control mode
    # --------------------------------------------------------------------------
    SC.outserv  = 0
    PFC.outserv = 0
    SC.i_ctrl   = 1    # Reactive Power Control
    SC.qu_char  = 0    # Const. Q
    PFC.i_net   = 1    # Power-Frequency Control

    # --------------------------------------------------------------------------
    # 7.8  Create operational scenarios up front (one per study case)
    # --------------------------------------------------------------------------
    opscen_objects = {}
    if not run_sim_only:
        app.PrintPlain(f"\nCreating {len(study_cases)} operational scenarios...")
        for sc_cfg_item in study_cases:
            name     = sc_cfg_item["name"]
            sim_type = sc_cfg_item["type"]

            type_folders = opscen_folder.GetContents(f"{sim_type}.IntFolder")
            target_folder = (type_folders[0] if type_folders
                             else opscen_folder.CreateObject("IntFolder", sim_type))

            existing = target_folder.GetContents(f"{name}.IntScenario")
            if existing:
                opscen_objects[name] = existing[0]
                app.PrintPlain(f"  [exists]  '{name}'")
            else:
                opscen_objects[name] = target_folder.CreateObject("IntScenario", name)
                app.PrintPlain(f"  [created] '{name}'")

    # --------------------------------------------------------------------------
    # 7.9  Main loop — one iteration per study case
    #
    #   Each case is wrapped in a try/except so a single bad case cannot kill
    #   the run. Failures are recorded in the 'failed' dict with the reason.
    # --------------------------------------------------------------------------
    app.PrintPlain(f"\nProcessing {len(study_cases)} study case(s)...")

    failed = {}   # {case_name: reason_string}
    passed = []   # [case_name]

    for sc_cfg_item in study_cases:
        sc_name  = sc_cfg_item["name"]
        sim_type = sc_cfg_item["type"]
        variant  = sc_cfg_item["variant"]   # 'Ramp' or 'Step'
        tc       = type_configs[sim_type]

        app.PrintPlain(f"\n  [{sc_name}]  type={sim_type}  variant={variant}")

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
                #    the scenario so the case is self-contained.
                # --------------------------------------------------------------
                study_case.Activate()
                gcc.activate_grid(app, netdat_folder, Ext_grid.loc_name)

                opscen = opscen_objects.get(sc_name)
                if opscen:
                    opscen.Activate()
                else:
                    app.PrintWarn(f"    Op scenario '{sc_name}' not found — skipping link.")

                PFC.psetp      = sc_cfg_item["psetp"]
                SC.qsetp       = sc_cfg_item["qsetp"]
                Ext_grid.usetp = sc_cfg_item["usetp"]

                if opscen:
                    try:
                        opscen.Save()
                    except Exception as e:
                        app.PrintWarn(f"    Could not save op scenario: {e}")

                # --------------------------------------------------------------
                # C. Load flow + RMS settings (timing from YAML)
                # --------------------------------------------------------------
                ldf    = app.GetFromStudyCase("ComLDF")
                comInc = app.GetFromStudyCase("ComInc")
                comSim = app.GetFromStudyCase("ComSim")

                gcc.configure_loadflow(ldf)
                configure_rms(comInc, comSim, sim_cfg, t_start_s, t_stop_s)

                # --------------------------------------------------------------
                # D. Step events — only for Step variant; Ramp uses external .txt
                # --------------------------------------------------------------
                if variant == "Step":
                    create_step_events(
                        app,
                        sim_type            = sim_type,
                        freq_voltage_source = freq_volt_source,
                        step_events         = tc["step_events"],
                    )
                else:
                    app.PrintPlain("    Ramp variant — events loaded from external .txt file.")

                # --------------------------------------------------------------
                # E. Record variables — everything plotted or evaluated
                # --------------------------------------------------------------
                comInc.Execute()
                elmRes = comInc.p_resvar
                add_recorded_results(elmRes, elm_objects, app)

                # --------------------------------------------------------------
                # F. Plot pages (x-ranges derived from YAML step sequence)
                # --------------------------------------------------------------
                setup_freq_plot_pages(
                    app, sim_type, variant, POC_BB, POC_BRK,
                    tc, t_start_s, t_stop_s, step_dur_s,
                )

            else:
                # --------------------------------------------------------------
                # run_sim_only — activate the existing study case from the run
                # folder resolved in 7.6 (NEVER recursively from the root),
                # then RE-APPLY the setpoints for this case.
                # --------------------------------------------------------------
                existing = sc_folder.GetContents(f"{sc_name}.IntCase")
                if not existing:
                    raise RuntimeError(
                        f"Study case '{sc_name}' not found in '{run_tag}'. "
                        f"Re-run with setup_only=True to recreate study cases."
                    )
                existing[0].Activate()
                gcc.activate_grid(app, netdat_folder, Ext_grid.loc_name)

                PFC.psetp      = sc_cfg_item["psetp"]
                SC.qsetp       = sc_cfg_item["qsetp"]
                Ext_grid.usetp = sc_cfg_item["usetp"]

            # ------------------------------------------------------------------
            # G. Run simulation (sequential, main thread only)
            #
            #   Hang protection: iopt_adapt=1 with a dtgrd_max cap forces PF
            #   to abandon a diverging case instead of shrinking the timestep
            #   indefinitely — and lets it stretch the step in quiet periods
            #   of these long runs. Result time base is therefore NON-UNIFORM:
            #   the evaluation tool must interpolate on time.
            #
            #   Failure layers (same as FRT script):
            #     1. ComInc non-zero return : initial conditions failed
            #     2. ComSim non-zero return : simulation failed to complete
            #     3. GetTotalWarnA() > 0    : fatal solver divergence (b:warnA)
            # ------------------------------------------------------------------
            if not setup_only:
                app.PrintPlain("    Running simulation...")
                comInc = app.GetFromStudyCase("ComInc")
                comSim = app.GetFromStudyCase("ComSim")

                comInc.iopt_adapt = 1
                comInc.dtgrd_max  = DTGRD_MAX_S

                err_inc = comInc.Execute()
                if err_inc:
                    raise RuntimeError(
                        f"Initial conditions did not converge (ComInc err={err_inc})."
                    )

                err_sim = comSim.Execute()
                if err_sim:
                    raise RuntimeError(
                        f"Simulation did not complete (ComSim err={err_sim})."
                    )

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
            reason = str(exc)
            failed[sc_name] = reason
            app.PrintWarn(f"    FAILED — {reason}")
            app.PrintWarn(f"    Skipping plots and CSV for this case.")
            try:
                app.GetActiveStudyCase().Deactivate()
            except Exception:
                pass

    # --------------------------------------------------------------------------
    # 7.10  Results export
    #        Handled EXCLUSIVELY by gcc_export_results.py with STUDY="FREQ"
    #        (one CSV per case + export_manifest.json).
    # --------------------------------------------------------------------------
    if not setup_only:
        app.PrintPlain(
            "\nResults export: run gcc_export_results.py (STUDY='FREQ') to "
            "write one CSV per case."
        )

    # --------------------------------------------------------------------------
    # 7.11  Run manifest — the interface contract for the evaluation tool
    # --------------------------------------------------------------------------
    manifest_path = gcc.write_run_manifest(
        out_dir, run_tag,
        proj_cfg      = proj_cfg,
        plant_summary = {
            "pmax_mw":   Pmax,
            "u_poc_kv":  U_POC,
            "q_factor":  Qfac,
            "qmax_mvar": Qmax,
            "qmin_mvar": Qmin,
        },
        gc_path     = gc_path,
        gc_meta     = gc_meta,
        study_cases = study_cases,
        failed      = failed,
        extra       = {
            "setup_only":     setup_only,
            "run_sim_only":   run_sim_only,
            "script_version": "2.0",
            "simulation_timing": {
                "t_start_s":       t_start_s,
                "t_stop_s":        t_stop_s,
                "step_duration_s": step_dur_s,
            },
        },
    )
    app.PrintPlain(f"\nRun manifest : {manifest_path}")

    # --------------------------------------------------------------------------
    # 7.12  Final summary
    # --------------------------------------------------------------------------
    elapsed  = round(clock.perf_counter() - t0, 2)
    n_total  = len(study_cases)
    n_passed = len(passed)
    n_failed = len(failed)

    app.PrintPlain("")
    app.PrintPlain("=" * 68)
    app.PrintPlain("  GCC FREQUENCY SIMULATION SUMMARY")
    app.PrintPlain("=" * 68)
    app.PrintPlain(f"  Run       : {run_tag}")
    app.PrintPlain(f"  Grid code : {gc_meta.get('grid_code', {}).get('name', '?')}  "
                   f"Type {gc_meta.get('module_type', '?')}")
    app.PrintPlain(f"  Types     : {applicable}")
    app.PrintPlain(f"  Runtime   : {elapsed}s")
    app.PrintPlain(f"  Cases     : {n_passed} / {n_total} solved")

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
        app.PrintPlain("  Export   : run gcc_export_results.py (STUDY='FREQ')")
    app.PrintPlain("=" * 68)


def main():
    app = powerfactory.GetApplication()
    # pf_session guarantees EchoOn() + Rebuild() even if _run raises,
    # so a config typo can never leave the PF user interface frozen.
    with gcc.pf_session(app):
        try:
            _run(app)
        except Exception as exc:
            app.PrintError(f"GCC Frequency script aborted: {exc}")
            raise


# ==============================================================================
# Entry point
# ==============================================================================
main()
