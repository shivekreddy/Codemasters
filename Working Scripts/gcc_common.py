# ==============================================================================
# GCC Automation Tool — Common Function Library
# ==============================================================================
# Shared functions used by gcc_frt_script.py and gcc_freq_script.py.
# All functions that exist in both scripts live here exactly once, so a fix
# lands in one place and both scripts stay in sync.
#
# HOW TO IMPORT FROM A ComPython SCRIPT
# -------------------------------------
# PowerFactory's embedded Python does not know where this file lives, so each
# script must add its own folder to sys.path before importing:
#
#     import sys, os
#     sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
#     import gcc_common as gcc
#
# Keep gcc_common.py in the same folder as the two scripts
# (e.g. ...\Codemasters\Working Scripts\).
#
# CONTENTS
# --------
#   1.  Plot colour constants            (both colour sets, names unchanged)
#   2.  YAML loading                     load_yaml
#   3.  PF object lookup                 get_folder, find_element,
#                                        activate_grid, find_study_case_recursive
#   4.  Run-folder helpers               make_run_tag, find_latest_run_folder
#   5.  Load flow configuration          configure_loadflow
#   6.  Plotting helpers                 set_xrange, add_x_ref_line, make_graph
#   0.  Raw data export location         RAW_DATA_ROOT, raw_data_dir
#   7.  CSV export (FindColumn-based)    export_case_to_csv, export_raw_results
#   8.  Run manifest                     write_run_manifest
#   9.  UI safety wrapper                pf_session
#
# WHAT IS VERBATIM AND WHAT CHANGED
# ---------------------------------
# Verbatim extractions (identical in both scripts as of 14-06-2026):
#   load_yaml, get_folder, find_element, activate_grid, configure_loadflow,
#   set_xrange (was _set_xrange), find_study_case_recursive (from freq script).
#
# Changed / new — review these before first use:
#   export_results_to_csv : REWRITTEN. Column indices are now resolved per case
#                           via ElmRes.FindColumn() instead of positional
#                           col_idx + 1, with a runtime self-check of every
#                           column mapping. Also: case lookup is scoped to the
#                           run folder (not the study-case root), rows are
#                           stream-written (not accumulated in memory), and
#                           ElmRes.Release() is called after each case.
#   write_run_manifest    : NEW. Dumps per-case metadata + status to JSON.
#   make_run_tag          : NEW. ISO timestamp (sorts correctly as a string).
#   find_latest_run_folder: NEW. Parses BOTH the old DD-MM-YYYY and the new
#                           ISO YYYY-MM-DD folder names, so run_sim_only picks
#                           the truly newest folder even across old runs.
#   make_graph            : Generalised (optional curve clearing + row/col
#                           placement) so both scripts can share it.
#   pf_session            : NEW. Context manager guaranteeing EchoOn() even
#                           when the script raises.
#
# Author  : GCC Automation Team

import os
import csv
import json
from datetime import datetime
from contextlib import contextmanager


# ==============================================================================
# 0.  RAW DATA EXPORT LOCATION   <<< SET THIS ONCE
# ==============================================================================
# Create this folder yourself first; the export scripts will not create it.
# Inside it they create (and refill on every export):
#     <RAW_DATA_ROOT>\FRT Raw Data\     <- gcc_export_frt.py
#     <RAW_DATA_ROOT>\Freq Raw Data\    <- gcc_export_freq.py
RAW_DATA_ROOT = r"C:\Users\SHIRED\Documents\xyz"

RAW_DATA_FOLDERS = {
    "FRT":  "FRT Raw Data",
    "FREQ": "Freq Raw Data",
}


def raw_data_dir(study, create=True):
    """
    Path of the raw data folder for 'FRT' or 'FREQ' inside RAW_DATA_ROOT.
    RAW_DATA_ROOT must already exist (it is created by hand, on purpose);
    the study subfolder is created when create=True.
    """
    if not os.path.isdir(RAW_DATA_ROOT):
        raise RuntimeError(
            f"Raw data folder not found:\n  {RAW_DATA_ROOT}\n"
            "Create it first, or set RAW_DATA_ROOT at the top of gcc_common.py."
        )
    path = os.path.join(RAW_DATA_ROOT, RAW_DATA_FOLDERS[study])
    if create:
        os.makedirs(path, exist_ok=True)
    return path


# ==============================================================================
# 1.  PLOT COLOUR CONSTANTS  (PowerFactory integer format)
# ==============================================================================
# FRT script set
C_BLUE   = -1172706.0
C_RED    = -15572290.0
C_GREEN  = -5285188.0
C_TEAL   = -14636533.0
C_ORANGE = -36096.0
C_REF    = 14            # dark gold/olive — fault / step reference lines

# Frequency script set
C_FREQ   = -556609.0     # purple/blue — frequency
C_VOLT   = -36096.0      # orange/gold — voltage
C_P      = -14636533.0   # teal        — active power
C_Q      = -15572290.0   # red         — reactive power


# ==============================================================================
# 1b.  RECORDED VARIABLES — single authoritative definitions
#      (element_key matches keys in cfg["elements"]; variable is the PF
#       result string; label becomes the CSV column header)
# ==============================================================================

RECORD_VARS_FRT = [
    # POC busbar — voltage only
    ("poc_busbar", "m:u:A",       "POC Voltage A [pu]"),
    ("poc_busbar", "m:u:B",       "POC Voltage B [pu]"),
    ("poc_busbar", "m:u:C",       "POC Voltage C [pu]"),
    ("poc_busbar", "m:u1",        "POC Pos-Seq Voltage [pu]"),
    ("poc_busbar", "m:u2",        "POC Neg-Seq Voltage [pu]"),
    # GU busbar (generating unit terminals): the Dutch thresholds for current
    # evaluation (10% / 0.15 pu residual voltage) refer to this voltage
    ("gu_busbar",  "m:u:A",       "GU Voltage A [pu]"),
    ("gu_busbar",  "m:u:B",       "GU Voltage B [pu]"),
    ("gu_busbar",  "m:u:C",       "GU Voltage C [pu]"),
    ("gu_busbar",  "m:u1",        "GU Pos-Seq Voltage [pu]"),
    ("gu_busbar",  "m:u2",        "GU Neg-Seq Voltage [pu]"),
    # POC breaker: power and current. The sign depends on the breaker's bus1
    # orientation in the model; the evaluation detects it per case.
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
# 2.  YAML LOADER
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


def use_external_grid(app, ext_grid, poc_bb):
    """
    Make the external grid the source at the POC, in the ACTIVE scenario:
      * ElmXnet in service, and the switch in its cubicle closed
      * every ElmVac connected to the POC busbar out of service
    Returns the names of the ElmVac objects taken out of service.
    """
    ext_grid.outserv = 0
    cub = getattr(ext_grid, "bus1", None)
    if cub is not None:
        for sw in cub.GetContents("*.StaSwitch"):
            sw.on_off = 1

    switched_off = []
    for vac in app.GetCalcRelevantObjects("*.ElmVac") or []:
        vac_cub = getattr(vac, "bus1", None)
        if vac_cub is not None and vac_cub.GetParent() == poc_bb:
            vac.outserv = 1
            switched_off.append(vac.loc_name)
    return switched_off


def use_voltage_source(app, ext_grid, poc_bb, source_vac):
    """
    Make source_vac (ElmVac) the source at the POC, in the ACTIVE scenario.
    Used by the frequency studies: the external grid has no frequency
    setpoint that can be stepped in RMS, the ElmVac does (F0Hz).
      * source_vac in service, and the switch in its cubicle closed
      * external grid out of service
      * every OTHER ElmVac connected to the POC busbar out of service
    Returns the names of the other ElmVac objects taken out of service.
    """
    source_vac.outserv = 0
    cub = getattr(source_vac, "bus1", None)
    if cub is not None:
        for sw in cub.GetContents("*.StaSwitch"):
            sw.on_off = 1

    ext_grid.outserv = 1

    switched_off = []
    for vac in app.GetCalcRelevantObjects("*.ElmVac") or []:
        if vac == source_vac:
            continue
        vac_cub = getattr(vac, "bus1", None)
        if vac_cub is not None and vac_cub.GetParent() == poc_bb:
            vac.outserv = 1
            switched_off.append(vac.loc_name)
    return switched_off


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
# 4.  RUN-FOLDER HELPERS
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


def _parse_run_tag(name, prefix):
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


def find_latest_run_folder(root_folder, prefix):
    """
    Return the most recent '<prefix>_*' IntFolder inside root_folder, or None.

    Folders are ranked by their PARSED timestamp, not by name, so the old
    DD-MM-YYYY folders and new ISO folders rank correctly side by side.
    (Plain string sort on DD-MM-YYYY picks the wrong folder across month
    boundaries — that bug is what this helper replaces.)
    Unparseable '<prefix>_*' folders are ignored with no error.
    """
    candidates = []
    for f in root_folder.GetContents(f"{prefix}_*.IntFolder"):
        ts = _parse_run_tag(f.loc_name, prefix)
        if ts is not None:
            candidates.append((ts, f))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[-1][1]


# ==============================================================================
# 5.  LOAD FLOW CONFIGURATION
# ==============================================================================

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


# ==============================================================================
# 6.  PLOTTING HELPERS
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


# ==============================================================================
# 7.  CSV EXPORT  — one study case -> one CSV file (FindColumn-based)
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
                f"not in result file, column filled with 0."
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
                    f"expected '{variable}'. Column filled with 0."
                )
                col_map[(elm_key, variable)] = None
                continue
        except Exception:
            pass   # GetVariable unavailable — trust FindColumn

        col_map[(elm_key, variable)] = col
    return col_map


# Backwards-compatible alias (pre-1.1 name)
_build_column_map = build_column_map


def export_case_to_csv(app, study_case, elm_objects, record_vars, csv_path):
    """
    Export ONE study case's result file to ONE CSV file. Always writes a file.

    CSV format:  time_s, <one column per record_vars entry>
    One row per time step. NOTE: the time base is NON-UNIFORM under
    iopt_adapt=1; anything consuming the CSV must interpolate on time_s,
    never index by row.

    Never fails for missing data:
      * no result file / no rows (case not simulated) -> one row of zeros at t=0
      * variable not recorded, or element not found  -> that column is all 0
    Column indices are resolved per case via FindColumn() with a self-check,
    never positional. ElmRes.Release() is always called afterwards.

    Returns (n_rows, last_t, missing_labels):
      n_rows         result rows read (0 if no data)
      last_t         last time point, None if no data
      missing_labels column labels that were filled with 0
    """
    header = ["time_s"] + [lbl for _, _, lbl in record_vars]
    all_labels = [lbl for _, _, lbl in record_vars]

    def write_zero_file():
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerow([0.0] + [0] * len(record_vars))
        return 0, None, all_labels

    study_case.Activate()
    comInc = app.GetFromStudyCase("ComInc")
    elmRes = getattr(comInc, "p_resvar", None) if comInc else None
    if elmRes is None:
        app.PrintWarn(f"  [{study_case.loc_name}] no result object, "
                      f"exported as zeros.")
        return write_zero_file()

    try:
        elmRes.Load()
        n_rows = elmRes.GetNumberOfRows()
        if not n_rows or n_rows <= 0:
            return write_zero_file()

        col_map = build_column_map(
            elmRes, elm_objects, record_vars, app, study_case.loc_name
        )
        missing = [lbl for elm_key, variable, lbl in record_vars
                   if col_map[(elm_key, variable)] is None]

        last_t = None
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)

            for i in range(n_rows):
                err_t, t = elmRes.GetValue(i)     # col omitted -> time column
                if err_t:
                    continue
                last_t = t

                row = [round(t, 6)]
                for elm_key, variable, _lbl in record_vars:
                    col = col_map[(elm_key, variable)]
                    if col is None:
                        row.append(0)
                        continue
                    err_v, val = elmRes.GetValue(i, col)
                    row.append(0 if err_v else repr(round(val, 10)))
                writer.writerow(row)

        if last_t is None:
            return write_zero_file()
        return n_rows, last_t, missing

    except Exception as e:
        app.PrintWarn(f"  [{study_case.loc_name}] could not read results "
                      f"({e}), exported as zeros.")
        return write_zero_file()

    finally:
        try:
            elmRes.Release()
        except Exception:
            pass


# Completeness tolerance basis per study: must match DTGRD_MAX_S in the
# simulation scripts (last result time must reach t_stop - 2 x dtgrd_max).
_EXPORT_DTGRD_MAX_S = {"FRT": 0.01, "FREQ": 0.1}


def resolve_config_path(script_file, config_file=None):
    """config_file (file OR folder), else the config next to script_file."""
    path = config_file or os.path.dirname(os.path.abspath(script_file))
    if os.path.isdir(path):
        for name in ("project_config.yaml", "project_config_update.yaml"):
            candidate = os.path.join(path, name)
            if os.path.isfile(candidate):
                return candidate
    return path


def export_raw_results(app, study, script_file, run_tag=None, config_file=None):
    """
    Export one FRT or FREQ run to <RAW_DATA_ROOT>\\<FRT|Freq> Raw Data\\:
    one CSV per study case (named after the case) + export_manifest.json.

    run_tag None -> the latest FRT_* / FREQ_* run folder in Study Cases.
    Works without results: cases without data are exported as zeros and
    marked 'no_result_data' in the manifest (the evaluation skips them).
    """
    record_vars = RECORD_VARS_FRT if study == "FRT" else RECORD_VARS_FREQ
    tolerance   = 2.0 * _EXPORT_DTGRD_MAX_S[study]

    app.PrintPlain("=" * 68)
    app.PrintPlain(f"  GCC Raw Data Export ({study}) starting")
    app.PrintPlain("=" * 68)

    # --- Destination (checked first: a wrong path fails before any work) -----
    export_dir = raw_data_dir(study)
    app.PrintPlain(f"\nExport to : {export_dir}")

    # --- Config and elements (a missing element only zeroes its columns) -----
    config_path = resolve_config_path(script_file, config_file)
    app.PrintPlain(f"Config    : {config_path}")
    cfg      = load_yaml(config_path)
    elem_cfg = cfg.get("elements", {})

    classes = {"poc_busbar": ["ElmTerm"], "poc_breaker": ["ElmCoup"],
               "gu_busbar": ["ElmTerm"], "inverter": ["ElmGenstat", "ElmSym"]}
    elm_objects = {}
    app.PrintPlain("\nElements:")
    for elm_key in dict.fromkeys(k for k, _, _ in record_vars):
        name = elem_cfg.get(elm_key)
        obj = None
        if name:
            for cls in classes.get(elm_key, []):
                obj = find_element(app, cls, name, required=False)
                if obj is not None:
                    break
        elm_objects[elm_key] = obj
        if obj is not None:
            app.PrintPlain(f"  [ok] {elm_key:12s}: {obj.loc_name}")
        else:
            app.PrintWarn(f"  [--] {elm_key:12s}: '{name}' not found, its "
                          f"columns will be 0")

    # --- Run folder -------------------------------------------------------
    sc_root = get_folder(app, "study")
    if run_tag:
        found = sc_root.GetContents(f"{run_tag}.IntFolder")
        sc_folder = found[0] if found else None
    else:
        sc_folder = find_latest_run_folder(sc_root, study)
    if sc_folder is None:
        raise RuntimeError(
            f"No {study} run folder found in Study Cases"
            + (f" named '{run_tag}'." if run_tag else ".")
            + f" Run the {study} simulation script first."
        )
    app.PrintPlain(f"\nRun folder: '{sc_folder.loc_name}'")

    # --- Case metadata from the run manifest, if the simulation wrote one ----
    meta, run_manifest = {}, {}
    out_dir = (cfg.get("results") or {}).get("output_dir")
    if out_dir:
        mpath = os.path.join(out_dir, f"{sc_folder.loc_name}_manifest.json")
        if os.path.isfile(mpath):
            with open(mpath, "r", encoding="utf-8") as f:
                run_manifest = json.load(f)
            meta = {c["name"]: c for c in run_manifest.get("cases", [])}
    if not meta:
        app.PrintWarn("Run manifest not found: case metadata (fault times, "
                      "setpoints) will be missing from the export manifest.")

    # --- Export every study case in the run folder ---------------------------
    cases = sorted(sc_folder.GetContents("*.IntCase"), key=lambda c: c.loc_name)
    if not cases:
        raise RuntimeError(f"Run folder '{sc_folder.loc_name}' has no study cases.")
    app.PrintPlain(f"Cases     : {len(cases)}\n")

    entries, n_data, n_zero = [], 0, 0
    for case in cases:
        name  = case.loc_name
        fname = case_csv_filename(name)
        entry = dict(meta.get(name, {"name": name}))
        n_rows, last_t, missing = export_case_to_csv(
            app, case, elm_objects, record_vars, os.path.join(export_dir, fname))

        entry["csv_file"]        = fname
        entry["n_rows"]          = n_rows
        entry["missing_columns"] = missing
        if last_t is None:
            entry["export_status"] = "no_result_data"
            n_zero += 1
            app.PrintWarn(f"  [{name}] -> {fname}  NO RESULTS, written as zeros")
            entries.append(entry)
            continue

        entry["export_status"] = "ok"
        entry["t_end_s"]       = round(last_t, 6)
        if entry.get("t_stop") is not None:
            entry["complete"] = last_t >= float(entry["t_stop"]) - tolerance
        n_data += 1
        app.PrintPlain(f"  [{name}] -> {fname}  ({n_rows} rows, "
                       f"t_end={last_t:.3f}s"
                       + (f", {len(missing)} column(s) zero" if missing else "")
                       + ")")
        entries.append(entry)

    manifest = {
        "study":          study,
        "run_tag":        sc_folder.loc_name,
        "exported":       datetime.now().isoformat(timespec="seconds"),
        "columns":        [{"element": e, "variable": v, "column": lbl}
                           for e, v, lbl in record_vars],
        "time_base_note": "NON-UNIFORM time base: interpolate on time_s.",
        "project":        run_manifest.get("project"),
        "plant":          run_manifest.get("plant"),
        "grid_code_file": run_manifest.get("grid_code_file"),
        "cases":          entries,
    }
    manifest_path = os.path.join(export_dir, "export_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)

    app.PrintPlain("")
    app.PrintPlain("=" * 68)
    app.PrintPlain(f"  {study} RAW DATA EXPORT SUMMARY")
    app.PrintPlain("=" * 68)
    app.PrintPlain(f"  Run      : {sc_folder.loc_name}")
    app.PrintPlain(f"  Cases    : {len(cases)} exported, {n_data} with results, "
                   f"{n_zero} as zeros (no results)")
    app.PrintPlain(f"  Folder   : {export_dir}")
    app.PrintPlain(f"  Manifest : {manifest_path}")
    app.PrintPlain("=" * 68)
    return export_dir


# ==============================================================================
# 8.  RUN MANIFEST
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
        output_dir    : folder for the manifest (same as the CSV)
        run_tag       : run identifier
        proj_cfg      : cfg['project'] dict, written as-is
        plant_summary : dict of resolved plant values (Pmax, U_POC, In, ...)
        gc_path       : path of the grid code YAML used
        gc_meta       : gc_cfg['meta'] dict, written as-is
        study_cases   : list of case dicts — written as-is plus status
        failed        : {case_name: reason}
        extra         : optional dict merged into the top level (e.g. csv path)

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


# ==============================================================================
# 9.  UI SAFETY WRAPPER
# ==============================================================================

@contextmanager
def pf_session(app, clear_output=True, maximize_output=True):
    """
    Context manager guaranteeing the PF user interface is unfrozen even when
    the script raises. Replaces bare EchoOff()/EchoOn() pairs, which leave the
    UI frozen on any unhandled exception (missing element, bad YAML, ...).

    Usage:
        app = powerfactory.GetApplication()
        with gcc.pf_session(app):
            _run(app)          # all script logic inside the with-block
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
