# ==============================================================================
# GCC FRT Study Case Setup Script — PowerFactory
# ==============================================================================
# Reads frt_config_NL.yaml (or any compliant config) and creates:
#   - Operational scenarios (one per study case, named identically)
#   - Study cases copied from GCC_BaseCase, renamed to FRT case names
#   - Pre-fault setpoints written to station controller and PFC per op scenario
#   - Fault events (apply + clear) per study case
#   - RMS simulation settings (ComInc / ComSim) per study case
#   - Two blank plot pages per study case (names from config plot_pages section)
#
# Usage:
#   1. Set CONFIG_FILE path below (or keep next to this script)
#   2. Point a ComPython object at this file and run from PowerFactory
#   3. Use setup_only: true in config to create everything without running sims
#
# Dependencies: PyYAML — install via:
#   "<PF Python path>\python.exe" -m pip install pyyaml
#
# Author  : <your name>
# Version : 2.16 (lv_busbar removed, GU_BB used for GU voltage plots)
# ==============================================================================

import powerfactory
import math
import os
import sys
import time as clock
from datetime import datetime

# ------------------------------------------------------------------------------
# CONFIG FILE PATH
# Edit this to point at your YAML config file.
# Using raw string (r"...") handles backslashes safely on Windows.
# ------------------------------------------------------------------------------
CONFIG_FILE    = r"C:\Projects\GCC Script Development\Python Scripts\FRTs\Scripts\frt_config_NL_Rev3.yaml"

# Name of the base study case to copy for each FRT case.
# This case must already exist in the PowerFactory study cases folder.
BASE_CASE_NAME = "GCC_BaseCase"

# ------------------------------------------------------------------------------
# PROJECT ELEMENTS
# Update these loc_name values when switching projects.
# Run diagnostic.py to find the correct names for your model.
#
# Auto-detected via description field (no loc_name needed):
#   POC busbar      — looked up by NAME_POC
#   External grid   — looked up by NAME_EXT_GRID
#   Station ctrl    — looked up by NAME_STATION_CTRL
#   PFC ctrl        — looked up by NAME_PFC_CTRL
#
# Manually defined (change per project):
#   gu_busbar, gu_busbar, inverter
# ------------------------------------------------------------------------------

# loc_name values for elements that are consistently named across projects.
# Update these if your model uses different names.
NAME_POC          = "POC"    # loc_name of the POC busbar (ElmTerm)
NAME_EXT_GRID     = "Grid"   # loc_name of the external grid (ElmXnet)
NAME_STATION_CTRL = "SC"     # loc_name of the station controller (ElmStactrl)
NAME_PFC_CTRL     = "PPC"    # loc_name of the PFC controller (ElmSecctrl)

# Elements that are project-specific — update loc_name per project.
ELEMENTS = {
    "gu_busbar":   "TS1-A 1",   # ElmTerm — HV side of main transformer
    "inverter":    "TS1-A 1 PV 1",           # ElmGenstat — representative inverter/generator
}


# ==============================================================================
# 1. YAML loader — install check with clear message
# ==============================================================================

def load_yaml(path):
    try:
        import yaml
    except ImportError:
        raise ImportError(
            "PyYAML is not installed.\n"
            "Open a Command Prompt and run:\n"
            r'  "C:\Program Files\DIgSILENT\PowerFactory 2025 SP4\Python\3.13\python.exe" -m pip install pyyaml'
            "\nthen restart PowerFactory."
        )
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"Config file not found:\n  {path}\n"
            "Check the CONFIG_FILE path at the top of this script."
        )
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ==============================================================================
# 2. Setpoint resolver
# ==============================================================================

def resolve(value, Pmax, Qmax, Qmin):
    """
    Resolve symbolic strings to float values:
      'Pmax' -> Pmax [MW]
      'Qmax' -> +q_factor * Pmax [Mvar]
      'Qmin' -> -q_factor * Pmax [Mvar]
    Plain numbers are cast to float and returned unchanged.
    """
    if isinstance(value, str):
        symbols = {"Pmax": Pmax, "Qmax": Qmax, "Qmin": Qmin}
        if value not in symbols:
            raise ValueError(
                f"Unknown setpoint symbol '{value}'. "
                f"Valid symbols: {list(symbols.keys())}"
            )
        return symbols[value]
    return float(value)


# ==============================================================================
# 3. Fault type mapper  (config string -> PowerFactory i_shc integer)
# ==============================================================================

FAULT_TYPE_MAP = {
    "3ph":   0,   # three-phase short circuit
    "2ph":   1,   # two-phase short circuit
    "1ph-g": 2,   # single phase to ground
    "2ph-g": 3,   # two-phase to ground
    "clear": 4,   # clear fault event
}


# ==============================================================================
# 4. PowerFactory element helpers
# ==============================================================================

def get_folder(app, key):
    folder = app.GetProjectFolder(key)
    if folder is None:
        raise RuntimeError(
            f"PowerFactory project folder '{key}' not found. "
            "Is a project open?"
        )
    return folder


def find_element(app, pf_class, loc_name, required=True):
    """
    Find a single PF element by class and loc_name (case-insensitive).
    Tries two search strategies:
      1. GetCalcRelevantObjects — fast, covers active network elements
      2. GetProjectFolder search — catches elements inside substations or
         compound models that are not calc-relevant (e.g. HV busbars in
         ElmSubstat containers)
    """
    # Strategy 1: calc-relevant objects (covers most elements)
    objs = app.GetCalcRelevantObjects(f"*.{pf_class}")
    match = next(
        (o for o in objs if getattr(o, "loc_name", "").upper() == loc_name.upper()),
        None,
    )

    # Strategy 2: full project search via network data folder
    if match is None:
        netdat = app.GetProjectFolder("netdat")
        if netdat:
            all_objs = netdat.GetContents(f"*.{pf_class}", 1)  # 1 = recursive
            match = next(
                (o for o in all_objs
                 if getattr(o, "loc_name", "").upper() == loc_name.upper()),
                None,
            )

    if match is None and required:
        raise RuntimeError(
            f"Element not found — class: {pf_class}, loc_name: '{loc_name}'.\n"
            "Run diagnostic.py to list available element names, then update "
            "the NAME_* constants or ELEMENTS dict at the top of this script."
        )
    return match


def find_first(app, pf_class, required=True):
    """Return the first object of pf_class found in the active network."""
    objs = app.GetCalcRelevantObjects(f"*.{pf_class}")
    if not objs and required:
        raise RuntimeError(f"No {pf_class} found in the active network.")
    return objs[0] if objs else None



def activate_grid(app, netdat_folder, grid_name):
    """
    Activate the Grid ElmNet so that operational scenarios can be linked
    to study cases. Falls back to the first ElmNet if name not matched.
    """
    for obj in netdat_folder.GetContents():
        if obj.loc_name == grid_name:
            obj.Activate()
            return
    # fallback
    for obj in netdat_folder.GetContents():
        if obj.GetClassName() == "ElmNet":
            obj.Activate()
            return
    app.PrintWarning("Could not activate Grid — op scenario linking may be incomplete.")


# ==============================================================================
# 5. Load flow configuration
# ==============================================================================

def configure_loadflow(ldf):
    """Set standard load flow options on the ComLDF object."""
    ldf.iopt_net    = 0   # AC balanced positive sequence
    ldf.iPST_at     = 1   # auto tap — phase shifters
    ldf.iopt_plim   = 1   # consider active power limits
    ldf.iopt_at     = 1   # auto tap — transformers
    ldf.iopt_asht   = 1   # auto tap — shunts
    ldf.iopt_lim    = 1   # consider reactive power limits
    ldf.iopt_pq     = 1   # voltage-dependent loads
    ldf.iopt_fls    = 0   # no feeder load scaling
    ldf.iopt_apdist = 1   # active power per secondary control


# ==============================================================================
# 6. RMS simulation configuration
# ==============================================================================

def configure_rms(comInc, comSim, sim_cfg, fault_type_int, t_stop):
    """
    Configure ComInc and ComSim for an RMS simulation.
    Unbalanced network representation is used for asymmetric faults.
    """
    comInc.iopt_sim   = "rms"
    comInc.iopt_show  = 0                               # don't show initial conditions
    comInc.iopt_adapt = 0                               # no automatic step size adaptation
    comInc.dtgrd      = sim_cfg["integration_step_s"]
    comInc.tstart     = sim_cfg["t_start_s"]
    comInc.iopt_lt    = sim_cfg["a_stable_integration"]

    # Asymmetric faults (1ph-g, 2ph, 2ph-g) require unbalanced representation
    comInc.iopt_net = "rst" if fault_type_int in (1, 2, 3) else "sym"

    comSim.tstop = t_stop


# ==============================================================================
# 7. Fault event creation - Need to revise this part, a bit off here
# ==============================================================================

def compute_fault_impedance(uret, usetp, nom_Z, ratio_R_X):
    """
    Calculate fault impedance (R_f, X_f) [Ohm] to achieve retained voltage
    uret [p.u.] at the POC. Uses near-bolted values for uret == 0.
    """
    if uret <= 0.01:
        return 0.001, 0.01
    fault_X = (uret / (usetp - uret)) * nom_Z
    fault_R = fault_X * ratio_R_X
    return fault_R, fault_X


def create_fault_event(app, busbar, time_s, fault_type_int, R_f, X_f):
    """Create a single EvtShc event in the active study case's event folder."""
    evt_folder     = app.GetFromStudyCase("Simulation Events/Fault.IntEvt")
    event          = evt_folder.CreateObject("EvtShc", busbar.loc_name)
    event.p_target = busbar
    event.time     = time_s
    event.i_shc    = fault_type_int
    if fault_type_int != 4:    # clear events carry no impedance
        event.R_f = R_f
        event.X_f = X_f
    return event


# ==============================================================================
# 8. Plot page setup
# ==============================================================================

def add_x_const(plot, time_s, color=1):
    """
    Add a vertical dashed reference line at time_s on the given plot.
    color: 1=black, 2=red, 5=dark gold
    """
    obj          = plot.CreateObject("VisXvalue", "x-constant")
    obj.cConst   = "X"
    obj.value    = time_s
    obj.style    = 6        # dashed line
    obj.color    = color
    obj.show     = 0        # line only, no intersections
    obj.iopt_lab = 3        # automatic label position
    return obj


def setup_plot_pages(app, fault_type_int, t_fault, t_clear, uret,
                     POC_BB, GU_BB, Inverter, Ext_grid):
    """
    Create and populate two plot pages per study case, replicating the
    manually configured plot layout exactly.

    PAGE 1 — 'POC'  (mirrors GU Terminal but uses POC_BB instead of GU_BB):
      Graph 1 — POC Phase Voltages   : m:u:A, m:u:B, m:u:C       (POC_BB)
      Graph 2 — POC Sequence Voltages: m:u1, m:u2                 (POC_BB)
      Graph 3 — POC Power            : m:Psum:bus1, m:Qsum:bus1   (POC_BB)
      Graph 4 — POC Current          : m:i1P:bus1, m:i1Q:bus1,
                                       m:i2Q:bus1                 (POC_BB)
                                       all normalised by In

    PAGE 2 — 'GU Terminal'  (exact replica of manually configured page):
      Graph 1 — GU Phase Voltages    : m:u:A, m:u:B, m:u:C       (GU_BB)
      Graph 2 — GU Sequence Voltages : m:u1, m:u2                 (GU_BB)
      Graph 3 — Inverter Power       : m:Psum:bus1, m:Qsum:bus1   (Inverter)
      Graph 4 — Inverter Current     : m:i1P:bus1, m:i1Q:bus1,
                                       m:i2Q:bus1                 (Inverter)
                                       all normalised by In

    Colours match your manual setup (PF integer colour format):
      -1172706   = blue
      -15572290  = red
      -5285188   = green
      -14636533  = dark blue/teal
      -36096     = orange/gold

    Reference lines on all graphs:
      style=2, color=14 (dark gold/olive dashed line)
      - vertical at t_fault  (fault start)
      - vertical at t_clear  (fault clearance)
    """
    desktop = app.GetGraphicsBoard()
    comInc  = app.GetFromStudyCase("ComInc")
    elmRes  = comInc.p_resvar

    # Colour constants — matched from your manual plot configuration
    C_BLUE      = -1172706.0
    C_RED       = -15572290.0
    C_GREEN     = -5285188.0
    C_TEAL      = -14636533.0
    C_ORANGE    = -36096.0
    C_REF_LINE  = 14          # reference line colour (dark gold/olive)

    def add_ref_lines(plot):
        """Add fault start and fault clear vertical reference lines."""
        for t in [t_fault, t_clear]:
            obj         = plot.CreateObject("VisXvalue", "x-constant")
            obj.cConst  = "X1"
            obj.value   = t
            obj.style   = 2
            obj.color   = C_REF_LINE
            obj.show    = 1

    def make_graph(page, title, elm_var_color, row=0, col=0):
        """
        Create a curve plot with the given curves and reference lines.
        elm_var_color : list of (element, variable, color) tuples
        row, col      : 0-indexed grid position on the page (2x2 layout)
        """
        plot = page.GetOrInsertCurvePlot(title, 1)
        ds   = plot.GetDataSeries()
        for elm, var, col_val in elm_var_color:
            ds.AddCurve(elm, var)
        # Apply colours after all curves are added
        ds.curveTableColor     = [c for _, _, c in elm_var_color]
        ds.curveTableLineStyle = [1] * len(elm_var_color)
        # Set 2x2 grid position
        try:
            plot.irow = row
            plot.icol = col
        except Exception:
            pass  # older PF versions may not support this — layout still works
        add_ref_lines(plot)
        plot.DoAutoScaleX()
        plot.DoAutoScaleY()
        return plot

    # -----------------------------------------------------------------------
    # PAGE 1 — POC  (same layout as GU Terminal, POC_BB replaces GU_BB)
    # -----------------------------------------------------------------------
    page_poc = desktop.GetPage("POC", 1, "GrpPage")
    page_poc.SetResults(elmRes)

    # 2x2 grid layout:  [G1 Voltage]  [G2 Sequence]
    #                   [G3 Power  ]  [G4 Current ]

    # Graph 1 — POC Phase Voltages      (row=0, col=0)
    make_graph(page_poc, "Curve plot", [
        (POC_BB, "m:u:A", C_BLUE),
        (POC_BB, "m:u:B", C_RED),
        (POC_BB, "m:u:C", C_GREEN),
    ], row=0, col=0)

    # Graph 2 — POC Sequence Voltages   (row=0, col=1)
    make_graph(page_poc, "Curve plot(1)", [
        (POC_BB, "m:u1", C_BLUE),
        (POC_BB, "m:u2", C_RED),
    ], row=0, col=1)

    # Graph 3 — POC Power               (row=1, col=0)
    make_graph(page_poc, "Curve plot(2)", [
        (POC_BB, "m:Psum:bus1", C_TEAL),
        (POC_BB, "m:Qsum:bus1", C_BLUE),
    ], row=1, col=0)

    # Graph 4 — POC Current             (row=1, col=1)
    make_graph(page_poc, "Curve plot(3)", [
        (POC_BB, "m:i1P:bus1", C_TEAL),
        (POC_BB, "m:i1Q:bus1", C_ORANGE),
        (POC_BB, "m:i2Q:bus1", C_GREEN),
    ], row=1, col=1)

    # Set 2x2 grid layout on POC page
    page_poc.SetLayoutMode(2)      # 2 = grid layout (arrange on grid)
    page_poc.numLayoutColumns = 2  # 2 columns -> 2x2 with 4 graphs

    # Set 2x2 grid layout on POC page
    page_poc.SetLayoutMode(2)      # 2 = grid layout (arrange on grid)
    page_poc.numLayoutColumns = 2  # 2 columns -> 2x2 with 4 graphs

    # -----------------------------------------------------------------------
    # PAGE 2 — GU Terminal  (exact replica of your manual configuration)
    # -----------------------------------------------------------------------
    page_gu = desktop.GetPage("GU Terminal", 1, "GrpPage")
    page_gu.SetResults(elmRes)

    # 2x2 grid layout:  [G1 Voltage]  [G2 Sequence]
    #                   [G3 Power  ]  [G4 Current ]

    # Graph 1 — GU Phase Voltages       (row=0, col=0)
    make_graph(page_gu, "Curve plot", [
        (GU_BB,   "m:u:A", C_BLUE),
        (GU_BB,   "m:u:B", C_RED),
        (GU_BB,   "m:u:C", C_GREEN),
    ], row=0, col=0)

    # Graph 2 — GU Sequence Voltages    (row=0, col=1)
    make_graph(page_gu, "Curve plot(1)", [
        (GU_BB,   "m:u1", C_BLUE),
        (GU_BB,   "m:u2", C_RED),
    ], row=0, col=1)

    # Graph 3 — Inverter Power          (row=1, col=0)
    make_graph(page_gu, "Curve plot(2)", [
        (Inverter, "m:Psum:bus1", C_TEAL),
        (Inverter, "m:Qsum:bus1", C_BLUE),
    ], row=1, col=0)

    # Graph 4 — Inverter Current        (row=1, col=1)
    make_graph(page_gu, "Curve plot(3)", [
        (Inverter, "m:i1P:bus1", C_TEAL),
        (Inverter, "m:i1Q:bus1", C_ORANGE),
        (Inverter, "m:i2Q:bus1", C_GREEN),
    ], row=1, col=1)

    # Set 2x2 grid layout on GU Terminal page
    page_gu.SetLayoutMode(2)      # 2 = grid layout (arrange on grid)
    page_gu.numLayoutColumns = 2  # 2 columns -> 2x2 with 4 graphs

    app.Rebuild(2)


# ==============================================================================
# 9. MAIN
# ==============================================================================

def main():

    # --------------------------------------------------------------------------
    # 9.1  PowerFactory initialisation
    # --------------------------------------------------------------------------
    app = powerfactory.GetApplication()
    app.ClearOutputWindow()
    app.SetOutputWindowState(1)
    app.EchoOff()

    app.PrintPlain("=" * 68)
    app.PrintPlain("  GCC FRT Setup Script — starting")
    app.PrintPlain("=" * 68)
    t0 = clock.perf_counter()

    # --------------------------------------------------------------------------
    # 9.2  Load and parse config
    # --------------------------------------------------------------------------
    app.PrintPlain(f"\nConfig : {CONFIG_FILE}")
    cfg = load_yaml(CONFIG_FILE)

    proj_cfg  = cfg["project"]
    plant_cfg = cfg["plant"]
    # elements section removed from YAML — now defined in ELEMENTS dict above
    sim_cfg   = cfg["simulation"]
    oc_list   = cfg["operating_conditions"]   # list of 45 op condition dicts
    sc_list   = cfg["study_cases"]            # list of 45 study case dicts
    plot_pages = cfg.get("plot_pages", ["POC", "GU Terminal"])

    # Build a lookup dict: op condition name -> dict
    oc_map = {oc["name"]: oc for oc in oc_list}

    # --------------------------------------------------------------------------
    # 9.3  Resolved plant values
    # --------------------------------------------------------------------------
    Pmax  = float(plant_cfg["pmax_mw"])
    U_POC = float(plant_cfg["poc_voltage_kv"])
    Qfac  = float(plant_cfg.get("q_factor", 0.33))
    Qmax  =  Qfac * Pmax
    Qmin  = -Qfac * Pmax
    In    =  Pmax / (math.sqrt(3) * U_POC)   # nominal current [kA]

    app.PrintPlain(f"\nProject  : {proj_cfg['name']}  |  Grid code: {proj_cfg['grid_code']}  |  PPM Type {proj_cfg.get('ppm_type','?')}")
    app.PrintPlain(f"Pmax     : {Pmax:.1f} MW")
    app.PrintPlain(f"U_POC    : {U_POC:.1f} kV")
    app.PrintPlain(f"In       : {In:.4f} kA")
    app.PrintPlain(f"Qmax     : +{Qmax:.2f} Mvar")
    app.PrintPlain(f"Qmin     : {Qmin:.2f} Mvar")

    # --------------------------------------------------------------------------
    # 9.4  Script behaviour flags
    # --------------------------------------------------------------------------
    setup_only   = bool(sim_cfg.get("setup_only",   True))
    run_sim_only = bool(sim_cfg.get("run_sim_only", False))

    app.PrintPlain(f"\nFlags    : setup_only={setup_only}  run_sim_only={run_sim_only}")

    # --------------------------------------------------------------------------
    # 9.5  PF folder references + timestamped subfolders
    #      All new study cases go into:  Study Cases\FRT_dd/mm/yyyy_hh:mm:ss
    #      All new op scenarios go into: Operational Scenarios\FRT_dd/mm/yyyy_hh:mm:ss
    # --------------------------------------------------------------------------
    sc_folder_root     = get_folder(app, "study")
    opscen_folder_root = get_folder(app, "scen")
    netdat_folder      = get_folder(app, "netdat")

    # Build the folder name using current date/time
    _now         = datetime.now()
    _folder_name = _now.strftime("FRT_%d-%m-%Y_%H-%M-%S")

    # Create the subfolders (IntFolder) inside each root folder
    sc_folder     = sc_folder_root.CreateObject("IntFolder", _folder_name)
    opscen_folder = opscen_folder_root.CreateObject("IntFolder", _folder_name)

    app.PrintPlain(f"\nRun folder : '{_folder_name}'")

    # --------------------------------------------------------------------------
    # 9.6  Resolve model elements
    #
    #  Auto-detected via description field (set once in PF, works across projects):
    #    POC busbar, external grid, station controller, PFC controller
    #
    #  Manually defined via ELEMENTS dict at the top of this script:
    #    GU busbar, LV busbar, inverter
    # --------------------------------------------------------------------------
    app.PrintPlain("\nLooking up model elements...")

    # Auto-detected by description field
    POC_BB   = find_element(app, "ElmTerm",    NAME_POC)
    Ext_grid = find_element(app, "ElmXnet",    NAME_EXT_GRID)
    SC       = find_element(app, "ElmStactrl", NAME_STATION_CTRL)
    PFC      = find_element(app, "ElmSecctrl", NAME_PFC_CTRL)

    # Manually defined — looked up by loc_name from ELEMENTS dict
    GU_BB       = find_element(app, "ElmTerm",    ELEMENTS["gu_busbar"])
    Inverter    = (find_element(app, "ElmGenstat", ELEMENTS["inverter"], required=False)
                   or find_element(app, "ElmSym", ELEMENTS["inverter"]))

    app.PrintPlain(f"  POC busbar   : {POC_BB.loc_name}")
    app.PrintPlain(f"  Ext grid     : {Ext_grid.loc_name}")
    app.PrintPlain(f"  Station ctrl : {SC.loc_name}")
    app.PrintPlain(f"  PFC ctrl     : {PFC.loc_name}")
    app.PrintPlain(f"  GU busbar    : {GU_BB.loc_name}")
    app.PrintPlain(f"  Inverter     : {Inverter.loc_name}")

    # --------------------------------------------------------------------------
    # 9.7  Fault impedance base values (from external grid)
    # --------------------------------------------------------------------------
    ratio_R_X  = Ext_grid.rntxn
    extgrid_sc = Ext_grid.snss
    nom_Z      = (U_POC ** 2) / extgrid_sc

    app.PrintPlain(f"\nExt grid : Ssc={extgrid_sc:.1f} MVA  R/X={ratio_R_X:.4f}  Z_nom={nom_Z:.5f} Ohm")

    # --------------------------------------------------------------------------
    # 9.8  Ensure controllers are in service and in correct control mode
    # --------------------------------------------------------------------------
    SC.outserv  = 0    # in service
    PFC.outserv = 0    # in service
    SC.i_ctrl   = 1    # Reactive Power Control
    SC.qu_char  = 0    # Const. Q
    PFC.i_net   = 1    # Power-Frequency Control

    # --------------------------------------------------------------------------
    # 9.9  Create all operational scenarios up front
    # --------------------------------------------------------------------------
    app.PrintPlain(f"\nCreating {len(oc_list)} operational scenario(s)...")

    opscen_objects = {}   # name -> IntScenario PF object

    if not run_sim_only:
        for oc in oc_list:
            oc_name = oc["name"]
            existing = opscen_folder.GetContents(f"{oc_name}.IntScenario")
            if existing:
                opscen_objects[oc_name] = existing[0]
                app.PrintPlain(f"  [exists]  '{oc_name}'")
            else:
                opscen = opscen_folder.CreateObject("IntScenario", oc_name)
                opscen_objects[oc_name] = opscen
                app.PrintPlain(f"  [created] '{oc_name}'")

    # --------------------------------------------------------------------------
    # 9.10  Main loop — one iteration per study case
    # --------------------------------------------------------------------------
    app.PrintPlain(f"\nProcessing {len(sc_list)} study case(s)...")

    for sc_cfg in sc_list:

        sc_name   = sc_cfg["name"]
        oc_name   = sc_cfg["operating_condition"]
        ft_str    = sc_cfg["fault_type"]               # e.g. "1ph-g"
        uret      = float(sc_cfg["uret_pu"])
        t_fault   = float(sc_cfg["fault_start_s"])
        t_clear   = float(sc_cfg["fault_clr_time_s"])
        t_stop    = float(sc_cfg["t_stop_s"])

        ft_int    = FAULT_TYPE_MAP.get(ft_str)
        if ft_int is None:
            raise ValueError(f"Unknown fault_type '{ft_str}' in study case '{sc_name}'.")

        # Resolve setpoints for this operating condition
        oc        = oc_map[oc_name]
        psetp     = resolve(oc["psetp"], Pmax, Qmax, Qmin)
        qsetp     = resolve(oc["qsetp"], Pmax, Qmax, Qmin)
        usetp     = float(oc["usetp"])

        app.PrintPlain(f"\n  [{sc_name}]")
        app.PrintPlain(f"    Op scenario : {oc_name}")
        app.PrintPlain(f"    Fault       : {ft_str}  Uret={uret:.2f}pu  t_fault={t_fault}s  t_clear={t_clear}s")
        app.PrintPlain(f"    Setpoints   : P={psetp:.1f}MW  Q={qsetp:.1f}Mvar  U={usetp:.2f}pu")

        if not run_sim_only:

            # ------------------------------------------------------------------
            # A. Copy GCC_BaseCase -> rename to FRT study case name
            #    This preserves all base case settings: network data, variants,
            #    simulation objects, graphics etc.
            #    If the case already exists it is reused (safe to re-run).
            # ------------------------------------------------------------------
            existing_sc = sc_folder.GetContents(f"{sc_name}.IntCase")
            if existing_sc:
                study_case = existing_sc[0]
                app.PrintPlain("    [exists] Study case — reusing.")
            else:
                # Find the base case — always search in the ROOT study cases
                # folder, not the timestamped subfolder
                base_matches = sc_folder_root.GetContents(f"{BASE_CASE_NAME}.IntCase")
                if not base_matches:
                    raise RuntimeError(
                        f"Base case '{BASE_CASE_NAME}' not found in the study cases folder.\n"
                        "Check BASE_CASE_NAME at the top of this script."
                    )
                base_case = base_matches[0]

                # Copy the base case into the same folder.
                # PowerFactory API: use AddCopy on the destination folder,
                # passing the source object. Returns the new copied object.
                study_case = sc_folder.AddCopy(base_case)
                if study_case is None:
                    raise RuntimeError(
                        f"AddCopy() returned None when copying '{BASE_CASE_NAME}'. "
                        "Check PowerFactory permissions and that the base case exists."
                    )

                # Rename the copy to the FRT study case name
                study_case.loc_name = sc_name
                app.PrintPlain(f"    [copied]  '{BASE_CASE_NAME}' -> '{sc_name}'")

            # ------------------------------------------------------------------
            # B. Activate study case and link operational scenario
            #    Sequence matters:
            #      1. Activate the study case
            #      2. Activate the Grid (ElmNet) so PF knows which network data
            #         to associate with the scenario
            #      3. Set controller values (these get saved into the op scenario)
            #      4. Activate the op scenario — this saves the current state
            # ------------------------------------------------------------------
            study_case.Activate()
            activate_grid(app, netdat_folder, Ext_grid.loc_name)

            # Set controller values BEFORE activating the op scenario so that
            # the scenario captures these as its saved state
            PFC.psetp      = psetp
            SC.qsetp       = qsetp
            Ext_grid.usetp = usetp

            # Activate (and save to) the linked op scenario
            if oc_name in opscen_objects:
                opscen_objects[oc_name].Activate()
            else:
                app.PrintWarning(f"    Op scenario '{oc_name}' not found — skipping link.")

            # ------------------------------------------------------------------
            # C. Load flow settings
            # ------------------------------------------------------------------
            ldf = app.GetFromStudyCase("ComLDF")
            configure_loadflow(ldf)

            # ------------------------------------------------------------------
            # D. RMS simulation settings
            # ------------------------------------------------------------------
            comInc = app.GetFromStudyCase("ComInc")
            comSim = app.GetFromStudyCase("ComSim")
            configure_rms(comInc, comSim, sim_cfg, ft_int, t_stop)

            # ------------------------------------------------------------------
            # E. Fault events
            #    Apply fault at t_fault, clear at t_clear
            # ------------------------------------------------------------------
            R_f, X_f = compute_fault_impedance(uret, usetp, nom_Z, ratio_R_X)
            app.PrintPlain(f"    Fault Z     : R={R_f:.5f}  X={X_f:.5f} Ohm")

            create_fault_event(app, POC_BB, t_fault, ft_int,  R_f, X_f)   # apply
            create_fault_event(app, POC_BB, t_clear, 4,       R_f, X_f)   # clear

            # ------------------------------------------------------------------
            # F. Plot pages with curves and reference lines
            # ------------------------------------------------------------------
            setup_plot_pages(
                app,
                fault_type_int = ft_int,
                t_fault        = t_fault,
                t_clear        = t_clear,
                uret           = uret,
                POC_BB         = POC_BB,
                GU_BB          = GU_BB,
                Inverter       = Inverter,
                Ext_grid       = Ext_grid,
            )
            app.PrintPlain(f"    Plot pages  : POC, GU (curves configured)")

        else:
            # run_sim_only — activate the existing study case only
            existing_sc = sc_folder.GetContents(f"{sc_name}.IntCase")
            if not existing_sc:
                app.PrintWarning(f"    run_sim_only=True but '{sc_name}' not found — skipping.")
                continue
            existing_sc[0].Activate()

        # ----------------------------------------------------------------------
        # G. Run simulation (skipped when setup_only=True)
        # ----------------------------------------------------------------------
        if not setup_only:
            app.PrintPlain("    Running simulation...")
            comInc = app.GetFromStudyCase("ComInc")
            comSim = app.GetFromStudyCase("ComSim")

            err = comInc.Execute()
            if err:
                app.PrintWarning(f"    Initial conditions failed (err={err}).")
                continue

            err = comSim.Execute()
            if err:
                app.PrintWarning(f"    Simulation failed (err={err}).")
            else:
                app.PrintPlain("    Simulation complete.")

    # --------------------------------------------------------------------------
    # 9.11  Done
    # --------------------------------------------------------------------------
    app.Rebuild(2)
    app.EchoOn()

    elapsed = round(clock.perf_counter() - t0, 2)
    app.PrintPlain("\n" + "=" * 68)
    app.PrintPlain(f"  Setup complete — {len(sc_list)} study cases processed in {elapsed}s")
    app.PrintPlain(f"  Grid code : {proj_cfg['grid_code']}  |  PPM Type {proj_cfg.get('ppm_type','?')}")
    app.PrintPlain("=" * 68)


# ==============================================================================
# Entry point
# ==============================================================================
if __name__ == "__main__":
    main()