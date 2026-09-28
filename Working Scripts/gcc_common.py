# ==============================================================================
# GCC Automation Tool — Common Function Library
# ==============================================================================
# Everything shared by the simulation scripts (gcc_frt_script.py,
# gcc_freq_script.py) and the exporters (gcc_export_results*.py) lives here
# exactly once, so a fix lands in one place and every script stays in sync.
#
# HOW TO IMPORT FROM A ComPython SCRIPT
# -------------------------------------
# PowerFactory's embedded Python does not know where this file lives, so each
# script adds its own folder to sys.path before importing:
#
#     import sys, os
#     sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
#     import gcc_common as gcc
#
# Keep gcc_common.py in the same folder as the scripts
# (e.g. ...\Codemasters\Working Scripts\).
#
# CONTENTS
# --------
#   1.  Plot colours, recorded variables, CSV naming
#   2.  Config loading          load_yaml, resolve_config_path, load_project,
#                               plant_values
#   3.  PF object lookup        get_folder, find_element, find_elements,
#                               activate_grid, find_study_case_recursive
#   4.  Run folders             make_run_tag, parse_run_tag,
#                               find_latest_run_folder, open_run_folder,
#                               select_run_folders
#   5.  Preflight               check_output_dir, find_base_case
#   6.  Study case setup        get_or_create, copy_base_case,
#                               set_controller_modes, activate_case,
#                               configure_loadflow, add_recorded_results
#   7.  Simulation              enable_hang_protection, run_simulation
#   8.  Plotting                set_xrange, add_x_ref_line, make_graph
#   9.  CSV export              build_column_map, export_case_to_csv,
#                               write_result_csv, export_run_cases,
#                               export_runs
#   10. Run manifest            write_run_manifest, load_run_manifest,
#                               write_export_manifest
#   11. Output + entry point    print_summary, pf_session, run_script
#
# Author  : GCC Automation Team
# Version : 2.0
#
# CHANGELOG 1.1 -> 2.0
# --------------------
#   * Shared plumbing that was still duplicated in the FRT and frequency
#     scripts moved here: config path resolution, project/grid code loading,
#     plant values, element lookup, run folder handling, output-dir check,
#     base-case lookup, case copy, setpoint application, hang protection,
#     three-layer simulation run, result recording, summary, entry point.
#   * Run-folder selection (select_run_folders) shared by both exporters.
#   * Run manifest now records dtgrd_max_s so the exporters check case
#     completeness against the timestep the case actually ran with.
#   * parse_run_tag is public (was _parse_run_tag; alias kept).
# ==============================================================================

import os
import csv
import json
from datetime import datetime
from contextlib import contextmanager


# ==============================================================================
# 1.  PLOT COLOURS, RECORDED VARIABLES, CSV NAMING
# ==============================================================================

# Plot colours (PowerFactory integer format) — FRT set
C_BLUE   = -1172706.0
C_RED    = -15572290.0
C_GREEN  = -5285188.0
C_TEAL   = -14636533.0
C_ORANGE = -36096.0
C_REF    = 14            # dark gold/olive — fault / step reference lines

# Frequency set
C_FREQ   = -556609.0     # purple/blue — frequency
C_VOLT   = -36096.0      # orange/gold — voltage
C_P      = -14636533.0   # teal        — active power
C_Q      = -15572290.0   # red         — reactive power

# Recorded variables — single authoritative definitions.
# (element_key matches keys in cfg["elements"]; variable is the PF result
#  string; label becomes the CSV column header)
RECORD_VARS_FRT = [
    # POC busbar — voltage only
    ("poc_busbar", "m:u:A",       "POC Voltage A [pu]"),
    ("poc_busbar", "m:u:B",       "POC Voltage B [pu]"),
    ("poc_busbar", "m:u:C",       "POC Voltage C [pu]"),
    ("poc_busbar", "m:u1",        "POC Pos-Seq Voltage [pu]"),
    ("poc_busbar", "m:u2",        "POC Neg-Seq Voltage [pu]"),
    # POC breaker — power and current (load convention at the grid:
    # P negative = plant generating, Q positive = capacitive/leading export)
    ("poc_breaker", "m:Psum:bus1", "POC Active Power [MW]"),
    ("poc_breaker", "m:Qsum:bus1", "POC Reactive Power [Mvar]"),
    ("poc_breaker", "m:i1P:bus1",  "POC Active Current [kA]"),
    ("poc_breaker", "m:i1Q:bus1",  "POC Reactive Current [kA]"),
    ("poc_breaker", "m:i2Q:bus1",  "POC Neg-Seq Reactive Current [kA]"),
    # Inverter (generator convention — signs opposite to the breaker)
    ("inverter",    "m:Psum:bus1", "Inverter Active Power [MW]"),
    ("inverter",    "m:Qsum:bus1", "Inverter Reactive Power [Mvar]"),
    ("inverter",    "m:i1P:bus1",  "Inverter Active Current [kA]"),
    ("inverter",    "m:i1Q:bus1",  "Inverter Reactive Current [kA]"),
]

RECORD_VARS_FREQ = [
    ("poc_busbar",  "m:fehz",      "POC Frequency [Hz]"),
    ("poc_busbar",  "m:u1",        "POC Pos-Seq Voltage [pu]"),
    ("poc_breaker", "m:Psum:bus1", "POC Active Power [MW]"),
    ("poc_breaker", "m:Qsum:bus1", "POC Reactive Power [Mvar]"),
]


def case_csv_filename(case_name):
    """
    The CSV file-naming convention (the evaluation tool relies on this):
        "FRT 3PH 0.05 Q0" -> "FRT_3PH_0.05_Q0.csv"
        "LFSMO Step"      -> "LFSMO_Step.csv"
    Spaces become underscores; everything else is kept as-is.
    """
    return case_name.replace(" ", "_") + ".csv"


# ==============================================================================
# 2.  CONFIG LOADING
# ==============================================================================

def load_yaml(path):
    """Load a YAML file, with a clear error if PyYAML is not installed."""
    try:
        import yaml
    except ImportError:
        raise ImportError(
            "PyYAML is not installed.\n"
            r'Run:  "<PF Python>\python.exe" -m pip install pyyaml'
            "\nthen restart PowerFactory."
        )
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Config file not found:\n  {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_config_path(config_file, script_file):
    """
    Resolve a script's CONFIG_FILE setting to a project config path.
      * None           -> project_config.yaml next to the script
      * a folder       -> project_config.yaml or project_config_update.yaml
                          inside it (first one found)
      * a file         -> used as-is
    """
    path = config_file or os.path.join(
        os.path.dirname(os.path.abspath(script_file)), "project_config.yaml")
    if os.path.isdir(path):
        for name in ("project_config.yaml", "project_config_update.yaml"):
            candidate = os.path.join(path, name)
            if os.path.isfile(candidate):
                return candidate
    return path


def load_project(config_path, app=None):
    """
    Load the project config and the grid code file it points to.
    A relative grid_code.file is resolved against the project config folder.

    Returns (cfg, gc_cfg, gc_path).
    """
    if app:
        app.PrintPlain(f"\nLoading project config : {config_path}")
    cfg = load_yaml(config_path)

    gc_path = cfg["grid_code"]["file"]
    if not os.path.isabs(gc_path):
        gc_path = os.path.join(os.path.dirname(config_path), gc_path)
    if app:
        app.PrintPlain(f"Loading grid code      : {gc_path}")
    return cfg, load_yaml(gc_path), gc_path


def plant_values(plant_cfg):
    """
    Resolved plant ratings used by both simulation scripts:
    Pmax, POC voltage, and the symmetric Q range  Qmax/Qmin = +/- q_factor * Pmax.
    """
    pmax = float(plant_cfg["pmax_mw"])
    qfac = float(plant_cfg.get("q_factor", 0.33))
    return {
        "pmax_mw":   pmax,
        "u_poc_kv":  float(plant_cfg["poc_voltage_kv"]),
        "q_factor":  qfac,
        "qmax_mvar":  qfac * pmax,
        "qmin_mvar": -qfac * pmax,
    }


# ==============================================================================
# 3.  POWERFACTORY OBJECT LOOKUP
# ==============================================================================

def get_folder(app, key):
    """Get a PowerFactory project folder by key, with a clear error."""
    folder = app.GetProjectFolder(key)
    if folder is None:
        raise RuntimeError(
            f"PF project folder '{key}' not found. Is a project open?"
        )
    return folder


def find_element(app, pf_class, loc_name, required=True):
    """
    Find a single PF element by class and loc_name (case-insensitive).
    Searches the full network data folder recursively first (works before any
    study case is active and finds elements inside compound models), then falls
    back to GetCalcRelevantObjects.
    """
    # Strategy 1: full recursive netdat search — most reliable
    netdat = app.GetProjectFolder("netdat")
    if netdat:
        all_objs = netdat.GetContents(f"*.{pf_class}", 1)   # 1 = recursive
        match = next(
            (o for o in all_objs
             if getattr(o, "loc_name", "").upper() == loc_name.upper()),
            None,
        )
        if match:
            return match

    # Strategy 2: calc-relevant objects (fast but misses inactive elements)
    objs  = app.GetCalcRelevantObjects(f"*.{pf_class}")
    match = next(
        (o for o in objs if getattr(o, "loc_name", "").upper() == loc_name.upper()),
        None,
    )
    if match:
        return match

    if required:
        raise RuntimeError(
            f"Element not found — class: {pf_class}, loc_name: '{loc_name}'.\n"
            "Check the 'elements' section of project_config.yaml."
        )
    return None


# PF class(es) for every element key in the 'elements' config section.
# Where several classes are listed they are tried in order
# (inverter: ElmGenstat for PV/storage, ElmSym for wind).
ELEMENT_CLASSES = {
    "poc_busbar":          ("ElmTerm",),
    "poc_breaker":         ("ElmCoup",),
    "gu_busbar":           ("ElmTerm",),
    "inverter":            ("ElmGenstat", "ElmSym"),
    "external_grid":       ("ElmXnet",),
    "station_ctrl":        ("ElmStactrl",),
    "pfc_ctrl":            ("ElmSecctrl",),
    "freq_voltage_source": ("ElmVac",),
}


def find_elements(app, elem_cfg, required, optional=()):
    """
    Look up the model elements named in the 'elements' config section.

    required : element keys that must be configured AND found
    optional : element keys that may be missing from the config or the model
               (returned as None)

    Returns {element_key: PF object or None} and prints one line per element.
    """
    elms = {}
    for key in required:
        if key not in elem_cfg:
            raise RuntimeError(f"Missing 'elements.{key}' in project config.")

    for key in list(required) + list(optional):
        is_required = key in required
        name = elem_cfg.get(key)
        obj  = None
        if name is not None:
            classes = ELEMENT_CLASSES[key]
            for i, pf_class in enumerate(classes):
                last = i == len(classes) - 1
                obj = find_element(app, pf_class, name,
                                   required=is_required and last)
                if obj:
                    break
        elms[key] = obj

        label = key.replace("_", " ")
        if obj:
            app.PrintPlain(f"  [ok] {label:20s}: {obj.loc_name}")
        else:
            app.PrintWarn(f"  [--] {label:20s}: not found / not configured")
    return elms


def activate_grid(app, netdat_folder, grid_name):
    """Activate the Grid ElmNet so op scenarios can be linked to study cases."""
    for obj in netdat_folder.GetContents():
        if obj.loc_name == grid_name:
            obj.Activate()
            return
    for obj in netdat_folder.GetContents():
        try:
            if obj.GetClassName() == "ElmNet":
                obj.Activate()
                return
        except Exception:
            pass
    app.PrintWarn("Could not activate Grid ElmNet.")


def find_study_case_recursive(folder, name):
    """Recursively search for a study case by loc_name."""
    matches = folder.GetContents(f"{name}.IntCase")
    if matches:
        return matches[0]
    for obj in folder.GetContents():
        try:
            if obj.GetClassName() == "IntFolder":
                result = find_study_case_recursive(obj, name)
                if result:
                    return result
        except Exception:
            pass
    return None


# ==============================================================================
# 4.  RUN FOLDERS
# ==============================================================================

# Old format used by runs created before this module existed.
_TAG_FMT_OLD = "%d-%m-%Y_%H-%M-%S"
# New ISO format — sorts chronologically as a plain string.
_TAG_FMT_ISO = "%Y-%m-%d_%H-%M-%S"


def make_run_tag(prefix):
    """
    Timestamped run tag, e.g. 'FRT_2026-07-05_14-31-08'.
    ISO date ordering so alphabetical sort == chronological sort.
    """
    return f"{prefix}_{datetime.now().strftime(_TAG_FMT_ISO)}"


def parse_run_tag(name, prefix):
    """
    Parse the timestamp out of a run-folder name. Accepts both the old
    DD-MM-YYYY format and the new ISO format. Returns datetime or None.
    """
    stem = name[len(prefix) + 1:] if name.startswith(prefix + "_") else None
    if not stem:
        return None
    for fmt in (_TAG_FMT_ISO, _TAG_FMT_OLD):
        try:
            return datetime.strptime(stem, fmt)
        except ValueError:
            continue
    return None


# Backwards-compatible alias (pre-2.0 name)
_parse_run_tag = parse_run_tag


def _run_folders_newest_first(root_folder, prefix):
    """All '<prefix>_*' IntFolders with a parseable timestamp, newest first."""
    candidates = []
    for f in root_folder.GetContents(f"{prefix}_*.IntFolder"):
        ts = parse_run_tag(f.loc_name, prefix)
        if ts is not None:
            candidates.append((ts, f))
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return [f for _, f in candidates]


def find_latest_run_folder(root_folder, prefix):
    """
    Return the most recent '<prefix>_*' IntFolder inside root_folder, or None.

    Folders are ranked by their PARSED timestamp, not by name, so the old
    DD-MM-YYYY folders and new ISO folders rank correctly side by side.
    Unparseable '<prefix>_*' folders are ignored with no error.
    """
    folders = _run_folders_newest_first(root_folder, prefix)
    return folders[0] if folders else None


def open_run_folder(app, prefix, run_sim_only):
    """
    Get the run folder for a simulation script.
      * setup (run_sim_only=False): create a new '<prefix>_<timestamp>' study
        case folder and a matching operation scenario folder
      * run_sim_only=True          : reuse the latest '<prefix>_*' study case
        folder (by parsed timestamp); no scenario folder is created

    Returns (run_tag, sc_folder, opscen_folder) — opscen_folder is None when
    reusing. Raises RuntimeError if run_sim_only finds no run folder.
    """
    sc_root = get_folder(app, "study")
    if run_sim_only:
        sc_folder = find_latest_run_folder(sc_root, prefix)
        if sc_folder is None:
            raise RuntimeError(
                f"run_sim_only=True but no {prefix}_* run folder found in "
                "Study Cases.\nRun the script with setup_only=True first to "
                "create study cases."
            )
        app.PrintPlain(f"\nRun folder : '{sc_folder.loc_name}'  [reusing "
                       f"existing — latest by parsed timestamp]")
        return sc_folder.loc_name, sc_folder, None

    run_tag       = make_run_tag(prefix)
    sc_folder     = sc_root.CreateObject("IntFolder", run_tag)
    opscen_folder = get_folder(app, "scen").CreateObject("IntFolder", run_tag)
    app.PrintPlain(f"\nRun folder : '{run_tag}'")
    return run_tag, sc_folder, opscen_folder


def select_run_folders(app, prefix, run_tag=None):
    """
    Decide which run folder(s) an exporter should export, in priority order:
      1. run_tag given            -> that folder only, no dialog
      2. one '<prefix>_*' folder  -> exported directly, no dialog
      3. several                  -> PF selection dialog, MULTI-SELECT
                                     supported; newest listed first;
                                     Cancel aborts cleanly

    Returns a list of IntFolder objects (never empty — raises instead).
    """
    sc_root = get_folder(app, "study")
    if run_tag:
        matches = sc_root.GetContents(f"{run_tag}.IntFolder")
        if not matches:
            raise RuntimeError(
                f"Run folder '{run_tag}' not found in Study Cases."
            )
        return [matches[0]]

    folders = _run_folders_newest_first(sc_root, prefix)
    if not folders:
        raise RuntimeError(
            f"No {prefix}_* run folder found in Study Cases. "
            f"Run the {prefix} simulation script first."
        )
    if len(folders) == 1:
        return folders

    app.PrintPlain(
        f"\n{len(folders)} {prefix} run folders found — select the run(s) to "
        f"export in the dialog (Ctrl-click for several; newest listed first)."
    )
    selected = app.ShowModalSelectBrowser(
        folders, f"Select the {prefix} run(s) to export")
    if not selected:
        raise RuntimeError("Export cancelled — no run folder selected.")
    return list(selected)


# ==============================================================================
# 5.  PREFLIGHT
# ==============================================================================

def check_output_dir(out_dir):
    """Create the output folder if needed and prove it is writable."""
    try:
        os.makedirs(out_dir, exist_ok=True)
        probe = os.path.join(out_dir, ".gcc_write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        raise RuntimeError(f"Output folder is not writable: {out_dir}\n{e}")


def find_base_case(app, elem_cfg, default="GCC_BaseCase"):
    """Find the base study case ('elements.base_case', else the default)."""
    name    = elem_cfg.get("base_case", default)
    matches = get_folder(app, "study").GetContents(f"{name}.IntCase")
    if not matches:
        raise RuntimeError(
            f"Base case '{name}' not found in Study Cases folder.\n"
            "Check 'elements.base_case' in project_config.yaml."
        )
    return matches[0]


# ==============================================================================
# 6.  STUDY CASE SETUP
# ==============================================================================

def get_or_create(folder, name, pf_class):
    """Return the object '<name>.<pf_class>' in folder, creating it if absent."""
    existing = folder.GetContents(f"{name}.{pf_class}")
    return existing[0] if existing else folder.CreateObject(pf_class, name)


def copy_base_case(app, sc_folder, base_case, name):
    """Copy the base case into the run folder as 'name' (reuse if present)."""
    existing = sc_folder.GetContents(f"{name}.IntCase")
    if existing:
        app.PrintPlain("    [exists] Study case — reusing.")
        return existing[0]
    study_case = sc_folder.AddCopy(base_case)
    if study_case is None:
        raise RuntimeError("AddCopy returned None — base case copy failed.")
    study_case.loc_name = name
    app.PrintPlain(f"    [copied] '{base_case.loc_name}' -> '{name}'")
    return study_case


def set_controller_modes(elms):
    """Put the station and P-f controllers in service in the study modes."""
    SC, PFC = elms["station_ctrl"], elms["pfc_ctrl"]
    SC.outserv  = 0
    PFC.outserv = 0
    SC.i_ctrl   = 1    # Reactive Power Control
    SC.qu_char  = 0    # Const. Q
    PFC.i_net   = 1    # Power-Frequency Control


def activate_case(app, study_case, elms, netdat_folder, case, opscen=None):
    """
    Activate a study case and write its pre-fault setpoints
    (case['psetp'], case['qsetp'], case['usetp']).

    If an operation scenario is given it is activated BEFORE the setpoints are
    written (activating afterwards could overlay them) and saved afterwards,
    so the case is self-contained when opened manually.
    """
    study_case.Activate()
    activate_grid(app, netdat_folder, elms["external_grid"].loc_name)

    if opscen:
        opscen.Activate()

    elms["pfc_ctrl"].psetp      = case["psetp"]
    elms["station_ctrl"].qsetp  = case["qsetp"]
    elms["external_grid"].usetp = case["usetp"]

    if opscen:
        try:
            opscen.Save()
        except Exception as e:
            app.PrintWarn(f"    Could not save op scenario: {e}")


def find_run_case(sc_folder, name):
    """Find a study case in THIS run folder only (never recursively)."""
    existing = sc_folder.GetContents(f"{name}.IntCase")
    if not existing:
        raise RuntimeError(
            f"Study case '{name}' not found in '{sc_folder.loc_name}'. "
            f"Re-run with setup_only=True to recreate study cases."
        )
    return existing[0]


def configure_loadflow(ldf):
    """Apply standard load flow settings."""
    ldf.iopt_net    = 0   # AC balanced positive sequence
    ldf.iPST_at     = 1   # auto tap — phase shifters
    ldf.iopt_plim   = 1   # consider active power limits
    ldf.iopt_at     = 1   # auto tap — transformers
    ldf.iopt_asht   = 1   # auto tap — shunts
    ldf.iopt_lim    = 1   # consider reactive power limits
    ldf.iopt_pq     = 1   # voltage-dependent loads
    ldf.iopt_fls    = 0   # no feeder load scaling
    ldf.iopt_apdist = 1   # active power per secondary control


def add_recorded_results(app, elmRes, elms, record_vars):
    """Add every record_vars variable to the active case's ElmRes."""
    for elm_key, variable, _label in record_vars:
        elm = elms.get(elm_key)
        if elm is None:
            app.PrintWarn(
                f"  Record: element '{elm_key}' not found — skipping '{variable}'"
            )
            continue
        elmRes.AddVariable(elm, variable)


# ==============================================================================
# 7.  SIMULATION
# ==============================================================================

def enable_hang_protection(comInc, dtgrd_max_s):
    """
    Solver hang protection: with iopt_adapt=1 and a dtgrd_max cap, PF abandons
    a diverging case instead of shrinking the timestep indefinitely.

    Set this during case CREATION, not only before running, so a case executed
    later by hand or from Task Automation carries the protection too.

    NOTE: the result time base becomes NON-UNIFORM — the evaluation tool must
    interpolate on time, never index by row.
    """
    comInc.iopt_adapt = 1
    comInc.dtgrd_max  = dtgrd_max_s


def run_simulation(app):
    """
    Run the active study case (main thread, blocking) with three failure
    layers. Raises RuntimeError on any of them:
      1. ComInc non-zero return : initial conditions failed
      2. ComSim non-zero return : simulation failed to complete
      3. GetTotalWarnA() > 0    : fatal solver divergence (b:warnA)
    b:warnB / b:warnC are reported as warnings only.
    """
    app.PrintPlain("    Running simulation...")
    comInc = app.GetFromStudyCase("ComInc")
    comSim = app.GetFromStudyCase("ComSim")

    err_inc = comInc.Execute()
    if err_inc:
        raise RuntimeError(
            f"Initial conditions did not converge (ComInc err={err_inc})."
        )

    err_sim = comSim.Execute()
    if err_sim:
        raise RuntimeError(f"Simulation did not complete (ComSim err={err_sim}).")

    warn_a = comSim.GetTotalWarnA()
    if warn_a > 0:
        raise RuntimeError(
            f"Simulation diverged — b:warnA={warn_a} fatal solver warning(s). "
            f"Results are unreliable."
        )

    warn_b = comSim.GetTotalWarnB()
    warn_c = comSim.GetTotalWarnC()
    if warn_b > 0:
        app.PrintWarn(f"    b:warnB={warn_b} — major convergence issues, "
                      f"review results carefully.")
    if warn_c > 0:
        app.PrintWarn(f"    b:warnC={warn_c} — minor convergence issues.")

    app.PrintPlain("    Simulation complete.")


def record_failure(app, failed, name, exc):
    """Record a failed case and deactivate it so PF stays clean."""
    failed[name] = str(exc)
    app.PrintWarn(f"    FAILED — {exc}")
    try:
        app.GetActiveStudyCase().Deactivate()
    except Exception:
        pass


# ==============================================================================
# 8.  PLOTTING
# ==============================================================================

def set_xrange(plot, x_min, x_max, app):
    """
    Set the x-axis range on a PltLinebarplot object.
    The x-axis is a PltAxis child object with attributes rangeMin and rangeMax.
    rangeMax MUST be set before rangeMin to avoid the PF validation error
    'min must be smaller than max'.
    """
    try:
        x_axis = plot.GetContents("x-Axis.PltAxis")
        if not x_axis:
            x_axis = plot.GetContents("*.PltAxis")
        if x_axis:
            ax = x_axis[0]
            ax.rangeMax = float(x_max)   # max first — see docstring
            ax.rangeMin = float(x_min)
            try:
                ax.iAutoScale = 0
            except Exception:
                pass
        else:
            app.PrintWarn(f"    x-Axis PltAxis not found on '{plot.loc_name}'")
    except Exception as e:
        app.PrintWarn(f"    Could not set x-axis range on '{plot.loc_name}': {e}")


def add_x_ref_line(plot, time_s, color=C_REF):
    """Add a vertical dashed hairline reference line at time_s."""
    obj        = plot.CreateObject("VisXvalue", "Constant")
    obj.cConst = "X1"
    obj.value  = float(time_s)
    obj.style  = 2
    obj.color  = color
    obj.show   = 1
    obj.width  = 0      # 0 = hairline
    return obj


def make_graph(page, title, elm_var_color_list,
               clear_existing=True, row=None, col=None, x_refs=None):
    """
    Create (or fetch) a curve plot on a page and add the specified curves.

    elm_var_color_list : list of (element, variable_string, color) tuples
    clear_existing     : remove existing curves first, so re-runs on the same
                         page always produce clean plots
    row, col           : optional grid placement on the page (2x2 layouts)
    x_refs             : optional list of times [s] for vertical reference lines

    Returns the plot object.
    """
    plot = page.GetOrInsertCurvePlot(title, 1)
    ds   = plot.GetDataSeries()

    if clear_existing:
        try:
            n = ds.GetNumberOfCurves() if hasattr(ds, "GetNumberOfCurves") else 0
            for i in range(n - 1, -1, -1):
                try:
                    ds.RemoveCurve(i)
                except Exception:
                    pass
        except Exception:
            pass

    for elm, var, _color in elm_var_color_list:
        ds.AddCurve(elm, var)
    ds.curveTableColor     = [c for _, _, c in elm_var_color_list]
    ds.curveTableLineStyle = [1] * len(elm_var_color_list)

    if row is not None and col is not None:
        try:
            plot.irow = row
            plot.icol = col
        except Exception:
            pass

    for t in (x_refs or []):
        add_x_ref_line(plot, t)

    plot.DoAutoScaleX()
    plot.DoAutoScaleY()
    return plot


def new_plot_page(app, name):
    """Get (or create) a plot page on the active case, bound to its ElmRes."""
    page = app.GetGraphicsBoard().GetPage(name, 1, "GrpPage")
    page.SetResults(app.GetFromStudyCase("ComInc").p_resvar)
    return page


def set_2x2_layout(page):
    """Arrange a page's plots in a 2-column grid."""
    try:
        page.SetLayoutMode(2)
        page.numLayoutColumns = 2
    except Exception:
        pass


# ==============================================================================
# 9.  CSV EXPORT  — one study case -> one CSV file (FindColumn-based)
# ==============================================================================

def build_column_map(elmRes, elm_objects, record_vars, app, sc_name):
    """
    Resolve the ElmRes column index for every (element, variable) pair in
    record_vars using ElmRes.FindColumn(). Requires elmRes.Load() first.

    Every resolved index is self-checked: GetVariable(col) must return the
    expected variable string. A mismatch is reported and the column is
    treated as missing rather than silently exporting the wrong signal.

    Returns { (elm_key, variable): column_index or None }
    """
    col_map = {}
    for elm_key, variable, _label in record_vars:
        elm = elm_objects.get(elm_key)
        if elm is None:
            col_map[(elm_key, variable)] = None
            continue

        col = elmRes.FindColumn(elm, variable)
        if col is None or col < 0:
            app.PrintWarn(
                f"  CSV export [{sc_name}]: '{variable}' for '{elm_key}' "
                f"not in result file — column left blank."
            )
            col_map[(elm_key, variable)] = None
            continue

        # Self-check: the column PF returned must actually carry this variable
        try:
            actual_var = elmRes.GetVariable(col)
            if actual_var and actual_var != variable:
                app.PrintWarn(
                    f"  CSV export [{sc_name}]: column self-check FAILED — "
                    f"FindColumn returned col {col} holding '{actual_var}', "
                    f"expected '{variable}'. Column left blank."
                )
                col_map[(elm_key, variable)] = None
                continue
        except Exception:
            pass   # GetVariable unavailable — trust FindColumn

        col_map[(elm_key, variable)] = col
    return col_map


# Backwards-compatible alias (pre-1.1 name)
_build_column_map = build_column_map


@contextmanager
def loaded_results(app, study_case):
    """Activate a case and yield its loaded ElmRes; always Release() after."""
    study_case.Activate()
    elmRes = app.GetFromStudyCase("ComInc").p_resvar
    elmRes.Load()
    try:
        yield elmRes
    finally:
        try:
            elmRes.Release()
        except Exception:
            pass


def write_result_csv(elmRes, csv_path, headers, columns):
    """
    Stream every row of a loaded ElmRes to CSV:  time_s, <columns...>
    columns : result column index per header (None -> blank column)
    Returns the last time value written (None if no row was readable).

    NOTE: the time base is NON-UNIFORM under iopt_adapt=1 — anything
    consuming the CSV must interpolate on time_s, never index by row.
    """
    last_t = None
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["time_s"] + list(headers))
        for i in range(elmRes.GetNumberOfRows()):
            err_t, t = elmRes.GetValue(i)     # col omitted -> time column
            if err_t:
                continue
            last_t = t
            row = [round(t, 6)]
            for col in columns:
                if col is None:
                    row.append("")
                    continue
                err_v, val = elmRes.GetValue(i, col)
                row.append("" if err_v else repr(round(val, 10)))
            writer.writerow(row)
    return last_t


def export_case_to_csv(app, study_case, elm_objects, record_vars, csv_path):
    """
    Export ONE study case's result file to ONE CSV file with the fixed
    record_vars columns (labels as headers). Column indices are resolved per
    case via FindColumn() with the runtime self-check — never positional.

    Returns (n_rows, last_t) — last_t is None if no data was read.
    """
    with loaded_results(app, study_case) as elmRes:
        n_rows = elmRes.GetNumberOfRows()
        if n_rows == 0:
            return 0, None
        col_map = build_column_map(
            elmRes, elm_objects, record_vars, app, study_case.loc_name)
        last_t = write_result_csv(
            elmRes, csv_path,
            [lbl for _, _, lbl in record_vars],
            [col_map[(k, v)] for k, v, _ in record_vars])
        return n_rows, last_t


def result_is_complete(last_t, t_stop, dtgrd_max_s):
    """
    True if a result file reaches t_stop. The time base is non-uniform under
    iopt_adapt=1, so the final sample lands NEAR t_stop — allow 2 x dtgrd_max.
    """
    return last_t >= float(t_stop) - 2.0 * float(dtgrd_max_s)


# dtgrd_max each simulation script runs with — used by the exporters for runs
# whose manifest predates the 'dtgrd_max_s' field.
DTGRD_MAX_DEFAULT = {"FRT": 0.01, "FREQ": 0.1}


def export_run_cases(app, sc_folder, cases, export_dir, export_case,
                     dtgrd_max_s):
    """
    Export every case of ONE run folder to one CSV per case.

    cases       : case dicts (manifest entries, or {'name', 'status'} only)
                  — failed cases are skipped, all metadata is carried through
    export_case : function(study_case, csv_path) -> (n_rows, last_t, info)
                  where info is a dict merged into the case's export entry
    dtgrd_max_s : timestep cap the run used — for the completeness check,
                  applied when the case carries t_stop

    Returns (entries, counts) — entries for export_manifest.json, counts
    {'ok', 'skip', 'err'}.
    """
    os.makedirs(export_dir, exist_ok=True)
    entries = []
    counts  = {"ok": 0, "skip": 0, "err": 0}

    def fail(entry, status, count="err"):
        entry["csv_file"] = None
        entry["export_status"] = status
        entries.append(entry)
        counts[count] += 1

    for case in cases:
        name  = case["name"]
        entry = dict(case)

        if case.get("status") == "failed":
            app.PrintWarn(f"  [{name}] skipped — failed in simulation "
                          f"({case.get('reason')})")
            fail(entry, "skipped_not_solved", "skip")
            continue

        matches = sc_folder.GetContents(f"{name}.IntCase")
        if not matches:
            app.PrintWarn(f"  [{name}] NOT FOUND in '{sc_folder.loc_name}' — skipped.")
            fail(entry, "case_not_found")
            continue

        fname    = case_csv_filename(name)
        csv_path = os.path.join(export_dir, fname)
        try:
            n_rows, last_t, info = export_case(matches[0], csv_path)
        except Exception as e:
            app.PrintWarn(f"  [{name}] export FAILED: {e}")
            fail(entry, f"export_error: {e}")
            continue

        if n_rows == 0 or last_t is None:
            app.PrintWarn(f"  [{name}] no result data — empty file removed.")
            try:
                os.remove(csv_path)
            except OSError:
                pass
            fail(entry, "no_result_data")
            continue

        # Completeness note (does not block the export)
        if case.get("t_stop") is not None:
            t_stop = float(case["t_stop"])
            entry["complete"] = result_is_complete(last_t, t_stop, dtgrd_max_s)
            if not entry["complete"]:
                app.PrintWarn(f"  [{name}] result ends at t={last_t:.3f}s of "
                              f"{t_stop:.1f}s — exported, but case looks "
                              f"incomplete.")

        entry.update(info)
        entry.update({"csv_file": fname, "export_status": "ok",
                      "n_rows": n_rows, "t_end_s": round(last_t, 6)})
        entries.append(entry)
        counts["ok"] += 1
        app.PrintPlain(f"  [{name}] -> {fname}  ({n_rows} rows, "
                       f"t_end={last_t:.3f}s)")

    return entries, counts


def export_runs(app, study, run_folders, export_fn):
    """
    Export each selected run folder with export_fn(sc_folder) -> summary dict
    ({'run_tag', 'export_dir', 'n_total', 'n_ok', 'n_skip', 'n_err', ...}).
    One run failing never stops the others. Prints the combined summary.
    """
    summaries, run_errors = [], {}
    for sc_folder in run_folders:
        try:
            summaries.append(export_fn(sc_folder))
        except Exception as e:
            run_errors[sc_folder.loc_name] = str(e)
            app.PrintWarn(f"  Run '{sc_folder.loc_name}' NOT exported: {e}")

    app.PrintPlain("")
    print_banner(app, f"{study} RESULTS EXPORT SUMMARY")
    app.PrintPlain(f"  Runs selected : {len(run_folders)}   "
                   f"exported: {len(summaries)}   failed: {len(run_errors)}")
    for s in summaries:
        line = f"  [ok] {s['run_tag']}  —  {s['n_ok']}/{s['n_total']} cases"
        if s["n_skip"]:
            line += f", {s['n_skip']} skipped"
        if s["n_err"]:
            line += f", {s['n_err']} errors"
        if s.get("had_manifest") is False:
            line += "  [no run manifest]"
        app.PrintPlain(line)
        app.PrintPlain(f"       -> {s['export_dir']}")
    for tag, reason in run_errors.items():
        app.PrintPlain(f"  [x]  {tag}  —  {reason.splitlines()[0]}")
    return summaries, run_errors


# ==============================================================================
# 10. RUN MANIFEST
# ==============================================================================

def write_run_manifest(output_dir, run_tag, proj_cfg, plant_summary,
                       gc_path, gc_meta, study_cases, failed,
                       extra=None):
    """
    Write a machine-readable JSON manifest of the run. This file is the
    interface contract between the simulation layer and the evaluation tool:
    the evaluator reads per-case metadata (uret, t_fault, t_clear, fault type,
    setpoints, status) from here instead of parsing case-name strings.

    Args:
        output_dir    : folder for the manifest
        run_tag       : run identifier
        proj_cfg      : cfg['project'] dict, written as-is
        plant_summary : dict of resolved plant values (Pmax, U_POC, In, ...)
        gc_path       : path of the grid code YAML used
        gc_meta       : gc_cfg['meta'] dict, written as-is
        study_cases   : list of case dicts — written as-is plus status
        failed        : {case_name: reason}
        extra         : optional dict merged into the top level

    Returns the manifest path.
    """
    os.makedirs(output_dir, exist_ok=True)
    manifest_path = os.path.join(output_dir, f"{run_tag}_manifest.json")

    cases = []
    for sc in study_cases:
        entry = dict(sc)
        entry["status"] = "failed" if sc["name"] in failed else "solved"
        entry["reason"] = failed.get(sc["name"])
        cases.append(entry)

    manifest = {
        "run_tag":        run_tag,
        "created":        datetime.now().isoformat(timespec="seconds"),
        "project":        proj_cfg,
        "plant":          plant_summary,
        "grid_code_file": gc_path,
        "grid_code_meta": gc_meta,
        "n_cases":        len(cases),
        "n_failed":       len(failed),
        "cases":          cases,
    }
    if extra:
        manifest.update(extra)

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)

    return manifest_path


def run_manifest_path(output_dir, run_tag):
    return os.path.join(output_dir, f"{run_tag}_manifest.json")


def load_run_manifest(output_dir, run_tag):
    """Load '{run_tag}_manifest.json' from output_dir. Returns (dict, path)
    or (None, path) if it does not exist."""
    path = run_manifest_path(output_dir, run_tag)
    if not os.path.isfile(path):
        return None, path
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f), path


def write_export_manifest(export_dir, content):
    """Write export_manifest.json — the evaluation tool's entry point."""
    path = os.path.join(export_dir, "export_manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(content, f, indent=2, default=str)
    return path


# ==============================================================================
# 11. OUTPUT + ENTRY POINT
# ==============================================================================

RULE = "=" * 68


def print_banner(app, title):
    app.PrintPlain(RULE)
    app.PrintPlain(f"  {title}")
    app.PrintPlain(RULE)


def print_summary(app, title, header_lines, n_total, failed, footer_lines=()):
    """
    End-of-run summary shared by the simulation scripts:
    title, header lines, solved count, failed-case list (for the OEM), footer.
    """
    app.PrintPlain("")
    print_banner(app, title)
    for line in header_lines:
        app.PrintPlain(f"  {line}")
    app.PrintPlain(f"  Cases     : {n_total - len(failed)} / {n_total} solved")

    app.PrintPlain("")
    if failed:
        app.PrintPlain(f"  FAILED CASES ({len(failed)})  — share with OEM:")
        app.PrintPlain("  " + "-" * 64)
        for name, reason in failed.items():
            app.PrintPlain(f"  x  {name}")
            app.PrintPlain(f"       Reason : {reason}")
        app.PrintPlain("  " + "-" * 64)
    else:
        app.PrintPlain("  All cases solved successfully.")

    for line in footer_lines:
        app.PrintPlain(line)
    app.PrintPlain(RULE)


@contextmanager
def pf_session(app, clear_output=True, maximize_output=True):
    """
    Context manager guaranteeing the PF user interface is unfrozen even when
    the script raises. Replaces bare EchoOff()/EchoOn() pairs, which leave the
    UI frozen on any unhandled exception (missing element, bad YAML, ...).
    """
    if clear_output:
        app.ClearOutputWindow()
    if maximize_output:
        app.SetOutputWindowState(1)
    app.EchoOff()
    try:
        yield app
    finally:
        app.EchoOn()
        try:
            app.Rebuild(2)
        except Exception:
            pass


def run_script(name, run_fn):
    """
    Standard ComPython entry point: get the PF application, run run_fn(app)
    inside pf_session (UI always unfrozen), and report an abort clearly.
    """
    import powerfactory
    app = powerfactory.GetApplication()
    with pf_session(app):
        try:
            run_fn(app)
        except Exception as exc:
            app.PrintError(f"{name} aborted: {exc}")
            raise
