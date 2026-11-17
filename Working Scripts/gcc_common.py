# ==============================================================================
# GCC Automation Tool — Common Function Library
# ==============================================================================
# Shared functions used by the scripts in Working Scripts\FRT and \Frequency.
# All functions that exist in both scripts live here exactly once, so a fix
# lands in one place and both scripts stay in sync.
#
# HOW TO IMPORT FROM A ComPython SCRIPT
# -------------------------------------
# PowerFactory's embedded Python does not know where this file lives, so each
# script must add this file's folder to sys.path before importing. The
# scripts sit in Working Scripts\FRT and Working Scripts\Frequency, this file
# and the project config in Working Scripts:
#
#     import sys, os
#     sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
#     import gcc_common as gcc
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
#   0.  Results folders                  results_dir, run_name_setting, latest_run_dir
#   7.  CSV export (FindColumn-based)    export_case_to_csv, export_raw_results
#   8.  Run manifest                     write_run_manifest
#   9.  UI safety wrapper                pf_session
#  10.  Plot page export (ComWr)         export_plots
#  11.  FRT raw data export              export_frt_raw
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
import re
import csv
import json
import fnmatch
from datetime import datetime
from contextlib import contextmanager


# ==============================================================================
# 0.  RESULTS FOLDERS: one source, the project config
# ==============================================================================
# Every script writes under ONE folder, set in the project config:
#
#     paths:
#       root: 'C:\...\Results Export'
#
# and creates these subfolders itself (missing folders are created):
#     Run Manifests\                       frt_1 / freq_1 (create and run cases)
#     FRT Raw Data\<FRT run>\<case>\*.csv   FRT\frt_3_export_raw_data.py
#     PowerFactory Plots\FRT\<FRT run>\     FRT\frt_4_export_plots.py
#     FRT Results\<FRT run>\               FRT\frt_5_compliance_check.py
#     Freq Raw Data\<FREQ run>\*.csv        Frequency\freq_2_export_raw_data.py
#     PowerFactory Plots\Frequency\<run>\  Frequency\freq_3_export_plots.py
#     Freq Results\<FREQ run>\             Frequency\freq_4_compliance_check.py
# <run> is the PowerFactory run folder name (FRT_<date>_<time>, may be
# renamed with text after it, e.g. "... (PPC Enabled)"). By default every
# script uses the LATEST run; paths.frt_run / paths.freq_run pick another.

RESULT_FOLDERS = {
    "manifests":    ("Run Manifests",),
    "frt_raw":      ("FRT Raw Data",),
    "freq_raw":     ("Freq Raw Data",),
    "frt_plots":    ("PowerFactory Plots", "FRT"),
    "freq_plots":   ("PowerFactory Plots", "Frequency"),
    "frt_results":  ("FRT Results",),
    "freq_results": ("Freq Results",),
}


def results_root(cfg):
    """paths.root from the project config (created if missing)."""
    root = ((cfg or {}).get("paths") or {}).get("root")
    if not root:
        raise RuntimeError("No 'paths: root:' in the project config. Add it, e.g.\n"
                           "  paths:\n    root: 'C:\\Users\\<you>\\Downloads\\Results Export'")
    os.makedirs(root, exist_ok=True)
    return root


def results_dir(cfg, kind, run=None, create=True):
    """
    Folder of one kind (see RESULT_FOLDERS) under paths.root, with the run
    folder name appended when given. Created when create=True.
    """
    path = os.path.join(results_root(cfg), *RESULT_FOLDERS[kind])
    if run:
        path = os.path.join(path, run)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def run_name_setting(cfg, study):
    """paths.frt_run / paths.freq_run: a run folder to use instead of the latest (or None)."""
    key = {"FRT": "frt_run", "FREQ": "freq_run"}[study]
    return ((cfg or {}).get("paths") or {}).get(key) or None


def latest_run_dir(parent, prefix):
    """
    The latest '<prefix>_<timestamp>[ anything]' subfolder (on disk) of
    parent, ranked by the timestamp in its name. None if there is none.
    """
    if not os.path.isdir(parent):
        return None
    runs = [(ts, n) for n in os.listdir(parent)
            if os.path.isdir(os.path.join(parent, n))
            and (ts := _parse_run_tag(n, prefix)) is not None]
    return os.path.join(parent, max(runs)[1]) if runs else None


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
    # Inverter (generator convention — signs opposite to the breaker).
    # Lower-case i1P / i1Q / i2Q are in p.u. (PowerFactory: "i1P in p.u.").
    # Must match everything plotted on the GU page(s).
    ("inverter",    "m:Psum:bus1", "Inverter Active Power [MW]"),
    ("inverter",    "m:Qsum:bus1", "Inverter Reactive Power [Mvar]"),
    ("inverter",    "m:i1P:bus1",  "Inverter Active Current [pu]"),
    ("inverter",    "m:i1Q:bus1",  "Inverter Reactive Current [pu]"),
    ("inverter",    "m:i2Q:bus1",  "Inverter Neg-Seq Reactive Current [pu]"),
]

# Plant 2 of a two-plant model (elements.gu_busbar2 / inverter2): exactly the
# plant 1 GU busbar and inverter variables, labelled "GU 2 ..." /
# "Inverter 2 ...". Only recorded and exported when plant 2 is configured —
# see record_vars_frt().
_PLANT2_KEYS = {"gu_busbar": ("gu_busbar2", "GU ", "GU 2 "),
                "inverter":  ("inverter2", "Inverter ", "Inverter 2 ")}
RECORD_VARS_FRT_PLANT2 = [
    (_PLANT2_KEYS[k][0], var, label.replace(_PLANT2_KEYS[k][1], _PLANT2_KEYS[k][2], 1))
    for k, var, label in RECORD_VARS_FRT if k in _PLANT2_KEYS
]

RECORD_VARS_FREQ = [
    ("poc_busbar",  "m:fehz",      "POC Frequency [Hz]"),
    ("poc_busbar",  "m:u1",        "POC Pos-Seq Voltage [pu]"),
    ("poc_breaker", "m:Psum:bus1", "POC Active Power [MW]"),
    ("poc_breaker", "m:Qsum:bus1", "POC Reactive Power [Mvar]"),
    # Other end of the (ideal) breaker: same power, generator oriented
    # (export positive). Plotted by the frequency script.
    ("poc_breaker", "m:Psum:bus2", "POC Active Power, export positive [MW]"),
    ("poc_breaker", "m:Qsum:bus2", "POC Reactive Power, export positive [Mvar]"),
]


# ------------------------------------------------------------------------------
# Plant elements (GU busbar + representative inverter per plant)
#   elements.gu_busbar1 / inverter1 : plant 1 (required for FRT)
#   elements.gu_busbar2 / inverter2 : plant 2, or null for a one-plant model
#   The old one-plant keys gu_busbar / inverter are still accepted as plant 1.
#   Record keys: plant 1 = 'gu_busbar' / 'inverter' (CSV columns unchanged),
#                plant 2 = 'gu_busbar2' / 'inverter2'.
# ------------------------------------------------------------------------------
_PLANT_CONFIG_KEYS = {
    "gu_busbar":  ("gu_busbar1", "gu_busbar"),
    "inverter":   ("inverter1",  "inverter"),
    "gu_busbar2": ("gu_busbar2",),
    "inverter2":  ("inverter2",),
}


def element_name(elem_cfg, elm_key):
    """The loc_name configured for an element key, or None if not set."""
    for key in _PLANT_CONFIG_KEYS.get(elm_key, (elm_key,)):
        if elem_cfg.get(key):
            return elem_cfg[key]
    return None


def plant_elements(elem_cfg):
    """
    [(gu_busbar name, inverter name), ...] — plant 1, plus plant 2 when
    configured. Raises if plant 1 is missing or plant 2 is half-configured.
    """
    plants = []
    for n, gu_key, inv_key in ((1, "gu_busbar", "inverter"),
                               (2, "gu_busbar2", "inverter2")):
        gu, inv = element_name(elem_cfg, gu_key), element_name(elem_cfg, inv_key)
        if gu and inv:
            plants.append((gu, inv))
        elif gu or inv or n == 1:
            raise RuntimeError(
                f"Plant {n}: set both 'elements.gu_busbar{n}' and "
                f"'elements.inverter{n}'"
                + (" (or set both to null for a one-plant model)." if n == 2
                   else "."))
    return plants


def record_vars_frt(elem_cfg):
    """FRT recorded variables for this model: plant 2 only if configured."""
    two_plants = bool(element_name(elem_cfg, "gu_busbar2")
                      or element_name(elem_cfg, "inverter2"))
    return RECORD_VARS_FRT + (RECORD_VARS_FRT_PLANT2 if two_plants else [])


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
    DD-MM-YYYY format and the new ISO format, also with text added after it
    when the folder was renamed by hand ('FRT_2026-11-11_13-56-23 (PPC
    Enabled)'). Returns datetime or None.
    """
    stem = name[len(prefix) + 1:] if name.startswith(prefix + "_") else None
    if not stem or len(stem) < 19:
        return None
    for fmt in (_TAG_FMT_ISO, _TAG_FMT_OLD):
        try:
            return datetime.strptime(stem[:19], fmt)
        except ValueError:
            continue
    return None


def run_tag_of(name, prefix):
    """'FRT_2026-11-11_13-56-23 (PPC Enabled)' -> 'FRT_2026-11-11_13-56-23' (or None)."""
    ts = _parse_run_tag(name, prefix)
    return None if ts is None else f"{prefix}_{name[len(prefix) + 1:][:19]}"


def find_latest_run_folder(root_folder, prefix):
    """
    Return the most recent '<prefix>_*' IntFolder inside root_folder, or None.

    Folders are ranked by their PARSED timestamp, not by name, so the old
    DD-MM-YYYY folders and new ISO folders rank correctly side by side.
    (Plain string sort on DD-MM-YYYY picks the wrong folder across month
    boundaries — that bug is what this helper replaces.)
    Unparseable '<prefix>_*' folders are ignored with no error; folders
    renamed with text after the timestamp are accepted.
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


def set_yrange(plot, y_min, y_max, app, axis_name="y-Axis"):
    """
    Set the (left) y-axis range of a PltLinebarplot, like set_xrange: the
    y-axis is a PltAxis child; rangeMax is set before rangeMin.
    """
    try:
        axes = plot.GetContents(f"{axis_name}.PltAxis")
        if not axes:
            axes = [a for a in plot.GetContents("*.PltAxis")
                    if not a.loc_name.lower().startswith("x")]
        if not axes:
            app.PrintWarn(f"    y-Axis PltAxis not found on '{plot.loc_name}'")
            return
        ax = axes[0]
        # PowerFactory rejects min >= max at every step: if the new range
        # lies below the current one, set the minimum first
        try:
            cur_min = float(ax.rangeMin)
        except Exception:
            cur_min = None
        if cur_min is not None and float(y_max) <= cur_min:
            ax.rangeMin = float(y_min)
            ax.rangeMax = float(y_max)
        else:
            ax.rangeMax = float(y_max)
            ax.rangeMin = float(y_min)
        try:
            ax.iAutoScale = 0
        except Exception:
            pass
    except Exception as e:
        app.PrintWarn(f"    Could not set y-axis range on '{plot.loc_name}': {e}")


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


# ------------------------------------------------------------------------------
# Curve legend labels: "<name> <description> (<unit>)",
#   e.g. "Inverter 1 Total Active Power (MW)"
# The name per element comes from the project config section plot_labels.
# ------------------------------------------------------------------------------
VARIABLE_LABELS = {
    "m:u:A":       ("Line-Ground Voltage A",              "p.u."),
    "m:u:B":       ("Line-Ground Voltage B",              "p.u."),
    "m:u:C":       ("Line-Ground Voltage C",              "p.u."),
    "m:u1":        ("Positive-Sequence Voltage",          "p.u."),
    "m:u2":        ("Negative-Sequence Voltage",          "p.u."),
    "m:Psum:bus1": ("Total Active Power",                 "MW"),
    "m:Qsum:bus1": ("Total Reactive Power",               "Mvar"),
    "m:i1P:bus1":  ("Positive-Sequence Active Current",   "p.u."),
    "m:i1Q:bus1":  ("Positive-Sequence Reactive Current", "p.u."),
    "m:i2Q:bus1":  ("Negative-Sequence Reactive Current", "p.u."),
    # Other side of the POC breaker (export positive), POC page of the FRT plots
    "m:Psum:bus2": ("Total Active Power",                 "MW"),
    "m:Qsum:bus2": ("Total Reactive Power",               "Mvar"),
    "m:i1P:bus2":  ("Positive-Sequence Active Current",   "p.u."),
    "m:i1Q:bus2":  ("Positive-Sequence Reactive Current", "p.u."),
    "m:i2Q:bus2":  ("Negative-Sequence Reactive Current", "p.u."),
    "m:fehz":      ("Frequency",                          "Hz"),
}

# PltDataseries attribute holding the user-defined legend label per curve
CURVE_LABEL_ATTR = "curveTableLabel"

# Curve-label errors seen by make_graph (reported once by the calling script)
LABEL_ERRORS = []


def curve_label(name, variable):
    """'<name> <description> (<unit>)' for a plotted variable."""
    desc, unit = VARIABLE_LABELS.get(variable, (variable, ""))
    return f"{name} {desc}" + (f" ({unit})" if unit else "")


# Default display names when plot_labels (or a key in it) is not set
_PLOT_LABEL_DEFAULTS = {
    "poc_busbar": "POC", "poc_breaker": "POC",
    "gu_busbar1": "GU 1", "inverter1": "Inverter 1",
    "gu_busbar2": "GU 2", "inverter2": "Inverter 2",
}


def plot_label_names(cfg):
    """
    Display names for the plot legends from the project config:
      plot_labels: {poc_busbar, poc_breaker, gu_busbar1, inverter1,
                    gu_busbar2, inverter2}
    Missing keys fall back to POC / GU 1 / Inverter 1 / GU 2 / Inverter 2.
    """
    given = cfg.get("plot_labels") or {}
    return {k: str(given.get(k) or default)
            for k, default in _PLOT_LABEL_DEFAULTS.items()}


def make_graph(page, title, elm_var_color_list,
               clear_existing=True, row=None, col=None, x_refs=None,
               labels=None):
    """
    Create (or fetch) a curve plot on a page and add the specified curves.

    elm_var_color_list : list of (element, variable_string, color) tuples
    labels             : optional legend label per curve (same order); written
                         to the data series' CURVE_LABEL_ATTR column
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
    if labels:
        try:
            setattr(ds, CURVE_LABEL_ATTR, list(labels))
        except Exception as e:
            LABEL_ERRORS.append(f"{title}: {e}")

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


# Completeness tolerance basis: must match DTGRD_MAX_S in the frequency
# simulation script (last result time must reach t_stop - 2 x dtgrd_max).
_EXPORT_DTGRD_MAX_S = {"FREQ": 0.1}


CONFIG_NAMES = ("project_config.yaml", "project_config_update.yaml")


def resolve_config_path(script_file, config_file=None):
    """
    config_file (file OR folder), else the project config next to
    script_file or one folder up (scripts in Working Scripts\\FRT and
    \\Frequency share the config in Working Scripts).
    """
    if config_file:
        folders = [config_file] if os.path.isdir(config_file) else []
        if not folders:
            return config_file
    else:
        here = os.path.dirname(os.path.abspath(script_file))
        folders = [here, os.path.dirname(here)]
    for folder in folders:
        for name in CONFIG_NAMES:
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate):
                return candidate
    return os.path.join(folders[0], CONFIG_NAMES[-1])


def export_raw_results(app, study, script_file, run_tag=None, config_file=None):
    """
    Export one frequency run to <paths.root>\\Freq Raw Data\\<run folder>\\:
    one CSV per study case (named after the case) + export_manifest.json.
    (FRT raw data: frt_3_export_raw_data.py, one folder per case.)

    run_tag None -> paths.freq_run from the project config, else the latest
    FREQ_* run folder in Study Cases. Works without results: cases without
    data are exported as zeros and marked 'no_result_data' in the manifest
    (the evaluation reports them).
    """
    if study != "FREQ":
        raise ValueError("export_raw_results exports frequency runs only; "
                         "use frt_3_export_raw_data.py for FRT.")
    tolerance   = 2.0 * _EXPORT_DTGRD_MAX_S[study]

    app.PrintPlain("=" * 68)
    app.PrintPlain(f"  GCC Raw Data Export ({study}) starting")
    app.PrintPlain("=" * 68)

    # --- Config and elements (a missing element only zeroes its columns) -----
    config_path = resolve_config_path(script_file, config_file)
    app.PrintPlain(f"Config    : {config_path}")
    cfg      = load_yaml(config_path)
    results_root(cfg)                    # a missing paths.root fails before any work
    run_tag  = run_tag or run_name_setting(cfg, study)
    elem_cfg = cfg.get("elements", {})
    record_vars = RECORD_VARS_FREQ

    classes = {"poc_busbar": ["ElmTerm"], "poc_breaker": ["ElmCoup"],
               "gu_busbar": ["ElmTerm"], "inverter": ["ElmGenstat", "ElmSym"],
               "gu_busbar2": ["ElmTerm"], "inverter2": ["ElmGenstat", "ElmSym"]}
    elm_objects = {}
    app.PrintPlain("\nElements:")
    for elm_key in dict.fromkeys(k for k, _, _ in record_vars):
        name = element_name(elem_cfg, elm_key)
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
    export_dir = results_dir(cfg, "freq_raw", sc_folder.loc_name)
    app.PrintPlain(f"Export to : {export_dir}")

    # --- Case metadata from the run manifest, if the simulation wrote one ----
    # (named after the run tag the folder had when it was created)
    meta, run_manifest = {}, {}
    man_dir = results_dir(cfg, "manifests", create=False)
    for tag in dict.fromkeys([sc_folder.loc_name, run_tag_of(sc_folder.loc_name, study)]):
        mpath = os.path.join(man_dir, f"{tag}_manifest.json") if tag else ""
        if tag and os.path.isfile(mpath):
            with open(mpath, "r", encoding="utf-8") as f:
                run_manifest = json.load(f)
            meta = {c["name"]: c for c in run_manifest.get("cases", [])}
            break
    if not meta:
        app.PrintWarn("Run manifest not found: case metadata (event times, "
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
        "p_factor":       run_manifest.get("p_factor"),
        "simulation_timing": run_manifest.get("simulation_timing"),
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


# ==============================================================================
# 10. PLOT PAGE EXPORT (ComWr): frt_4_export_plots.py / freq_3_export_plots.py
# ==============================================================================
# Writes the plot pages of one run folder (latest, or paths.frt_run /
# paths.freq_run) as image files under <paths.root>\PowerFactory Plots\:
#     FRT\<run>\<study case>\POC.png, GU Terminal 1.png, ...
#     Frequency\<run>\<study case> - Plot 1 - Main Results.png, ...
# Folders are created when missing; existing files are overwritten; cases
# without simulation results are skipped (listed in the summary).

# Per study: run-folder prefix, output folder kind, plot pages to export (as
# created by the frt_1 / freq_1 scripts; * = wildcard), and whether each study
# case gets its own folder.
PLOT_STUDIES = {
    "FRT": {
        "prefix": "FRT", "kind": "frt_plots",
        "pages": ["POC", "GU Terminal*"],
        "folder_per_case": True,
    },
    "Frequency": {
        "prefix": "FREQ", "kind": "freq_plots",
        "pages": ["* Plot 1 - *", "* Plot 2 - *", "* Plot 3 - *"],
        "folder_per_case": False,
    },
}

_BAD_CHARS = re.compile(r'[<>:"/\\|?*]')                     # not allowed in Windows names


def run_folder_for(app, cfg, prefix):
    """paths.frt_run / paths.freq_run from the config, else the latest '<prefix>_*' run folder."""
    study = app.GetProjectFolder("study")
    if study is None:
        raise RuntimeError("No Study Cases folder: activate the project first.")
    name = run_name_setting(cfg, prefix)
    if name:
        found = study.GetContents(f"{name}.IntFolder")
        if not found:
            raise RuntimeError(f"Run folder '{name}' (paths.{prefix.lower()}_run) not in Study Cases.")
        return found[0]
    return find_latest_run_folder(study, prefix)


def safe_name(name):
    """A PowerFactory name as a valid Windows file or folder name."""
    return _BAD_CHARS.sub("_", name).strip().rstrip(".")


def has_results(app):
    """True if the active case's result file holds simulation data."""
    inc = app.GetFromStudyCase("ComInc")
    elmRes = inc.p_resvar if inc is not None else None
    if elmRes is None:
        return False
    elmRes.Load()
    try:
        return elmRes.GetNumberOfRows() > 0
    finally:
        elmRes.Release()


def plot_pages(app, patterns):
    """The active case's plot pages matching the patterns, in pattern order."""
    board = app.GetGraphicsBoard()
    if board is None:
        return []
    pages = board.GetContents("*.GrpPage") or []
    out = []
    for pat in patterns:
        out += sorted((p for p in pages if fnmatch.fnmatch(p.loc_name, pat)
                       and p not in out), key=lambda p: p.loc_name)
    return out


def export_page(app, page, path, fmt="png"):
    """Show the page and write it to path with ComWr (overwrites). Error or None."""
    app.GetGraphicsBoard().Show(page)
    if os.path.isfile(path):
        os.remove(path)                       # overwrite, and a stale file can't fake success
    comWr = app.GetFromStudyCase("ComWr")
    comWr.iopt_rd    = fmt
    comWr.iopt_savas = 0                      # write to f without a dialog
    comWr.f          = path
    if comWr.Execute():
        return "export command failed"
    if not os.path.isfile(path):
        return "no file written (try another FORMAT, e.g. 'pdf' or 'wmf', in the export script)"
    return None


def export_study(app, label, spec, cfg, fmt="png"):
    """Export every case of the run folder of one study. Returns a summary."""
    app.PrintPlain(f"\n--- {label} " + "-" * (66 - len(label)))
    run_folder = run_folder_for(app, cfg, spec["prefix"])
    if run_folder is None:
        app.PrintWarn(f"  No {spec['prefix']}_* run folder in Study Cases: nothing exported.")
        return {"label": label, "files": 0, "out": None, "no_results": [], "failed": []}

    out_dir = results_dir(cfg, spec["kind"], safe_name(run_folder.loc_name))
    cases = sorted(run_folder.GetContents("*.IntCase"), key=lambda c: c.loc_name)
    app.PrintPlain(f"  Run folder : {run_folder.loc_name}   ({len(cases)} cases)")
    app.PrintPlain(f"  Output     : {out_dir}")

    n_files, no_results, failed = 0, [], []
    for case in cases:
        name = case.loc_name
        case.Activate()
        if not has_results(app):
            no_results.append(name)
            continue
        pages = plot_pages(app, spec["pages"])
        if not pages:
            failed.append((name, "no plot pages found ("
                           + ", ".join(spec["pages"]) + ")"))
            continue

        written = []
        for page in pages:
            if spec["folder_per_case"]:
                case_dir = os.path.join(out_dir, safe_name(name))
                os.makedirs(case_dir, exist_ok=True)
                path = os.path.join(case_dir, f"{safe_name(page.loc_name)}.{fmt}")
            else:
                # 'LFSMO Plot 1 - Main Results' -> 'LFSMO Step - Plot 1 - Main Results'
                short = re.sub(r"^.*?(?=Plot \d)", "", page.loc_name) or page.loc_name
                path = os.path.join(out_dir, f"{safe_name(name)} - {safe_name(short)}.{fmt}")
            error = export_page(app, page, path, fmt)
            if error:
                failed.append((f"{name} / {page.loc_name}", error))
            else:
                written.append(page.loc_name)
                n_files += 1
        if written:
            app.PrintPlain(f"  {name:28s} -> {', '.join(written)}")

    active = app.GetActiveStudyCase()
    if active is not None:
        active.Deactivate()
    return {"label": label, "files": n_files, "out": out_dir,
            "no_results": no_results, "failed": failed}


def export_plots(app, label, script_file, fmt="png"):
    """
    Export the plot pages of one study ('FRT' or 'Frequency') for the run
    chosen by the project config; prints a summary to the output window.
    """
    app.ClearOutputWindow()
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  GCC Export Plots ({label})")
    app.PrintPlain("=" * 72)
    try:
        config_file = resolve_config_path(script_file)
        cfg = load_yaml(config_file)
        root = results_root(cfg)
    except Exception as exc:
        app.PrintError(f"Project config / paths.root: {exc}")
        return None
    app.PrintPlain(f"  Config    : {config_file}")
    app.PrintPlain(f"  Directory : {os.path.join(root, *RESULT_FOLDERS[PLOT_STUDIES[label]['kind']])}")
    app.PrintPlain(f"  Format    : {fmt}")
    try:
        s = export_study(app, label, PLOT_STUDIES[label], cfg, fmt)
    except Exception as exc:
        app.PrintError(f"  {label} plot export aborted: {exc}")
        return None
    app.PrintPlain("")
    app.PrintPlain("=" * 72)
    app.PrintPlain("  SUMMARY")
    if s["out"] is None:
        app.PrintPlain("  No run folder found")
    else:
        app.PrintPlain(f"  {s['files']} plot(s) -> {s['out']}")
        if s["no_results"]:
            app.PrintPlain(f"  Skipped, no simulation results ({len(s['no_results'])}): "
                           + ", ".join(s["no_results"]))
        for what, error in s["failed"]:
            app.PrintWarn(f"  FAILED  {what}: {error}")
    app.PrintPlain("=" * 72)
    return s


# ==============================================================================
# 11. FRT RAW DATA EXPORT: frt_3_export_raw_data.py / frt_3b_export_selected_cases.py
# ==============================================================================
# One folder per study case, one CSV per FRT plot page holding exactly that
# page's variables (time_s first; headers "<plot label> <variable> (<unit>)").
# The pages and their elements come from the project config (elements +
# plot_labels): plant 2 configured -> "GU Terminal 1" + "GU Terminal 2",
# else one "GU Terminal" page.

# ------------------------------------------------------------------------------
# PAGE CONTENT — the same variables as the FRT plot pages
# ------------------------------------------------------------------------------
FRT_RAW_BUSBAR_VARS = ["m:u:A", "m:u:B", "m:u:C", "m:u1", "m:u2"]
FRT_RAW_SOURCE_VARS = ["m:Psum:bus1", "m:Qsum:bus1", "m:i1P:bus1", "m:i1Q:bus1",
               "m:i2Q:bus1"]


# PowerFactory classes per element role
_FRT_BUSBAR_CLASSES = ("ElmTerm",)
_FRT_BREAKER_CLASSES = ("ElmCoup",)
_FRT_INVERTER_CLASSES = ("ElmGenstat", "ElmSym")



# ==============================================================================
# 11a. Pages from the project config
# ==============================================================================

def frt_raw_pages(config_file):
    """
    The plot pages per study case, from the project config:
      [(page name, [(element loc_name, classes, legend name, variable), ...])]
    """
    if not os.path.isfile(config_file):
        raise RuntimeError(f"Project config not found:\n  {config_file}\n"
                           "It must be in Working Scripts, next to gcc_common.py.")
    cfg = load_yaml(config_file) or {}
    elem = cfg.get("elements")
    if not isinstance(elem, dict):
        raise RuntimeError(f"No top-level 'elements' section in:\n  {config_file}")
    given  = cfg.get("plot_labels") or {}
    labels = {k: str(given.get(k) or d) for k, d in _PLOT_LABEL_DEFAULTS.items()}

    def name(*keys):                      # first configured key wins
        for k in keys:
            if elem.get(k):
                return str(elem[k])
        return None

    def page(bus, bus_label, src, src_cls, src_label):
        return ([(bus, _FRT_BUSBAR_CLASSES, bus_label, v) for v in FRT_RAW_BUSBAR_VARS] +
                [(src, src_cls, src_label, v) for v in FRT_RAW_SOURCE_VARS])

    poc_bb, poc_brk = name("poc_busbar"), name("poc_breaker")
    if not poc_bb or not poc_brk:
        raise RuntimeError("Set elements.poc_busbar and elements.poc_breaker "
                           "in the project config.")
    pages = [("POC", page(poc_bb, labels["poc_busbar"], poc_brk,
                          _FRT_BREAKER_CLASSES, labels["poc_breaker"]))]

    plants = []
    for n, gu_keys, inv_keys in ((1, ("gu_busbar1", "gu_busbar"), ("inverter1", "inverter")),
                                 (2, ("gu_busbar2",), ("inverter2",))):
        gu, inv = name(*gu_keys), name(*inv_keys)
        if gu and inv:
            plants.append((n, gu, inv))
        elif gu or inv:
            raise RuntimeError(f"Plant {n}: set both gu_busbar{n} and inverter{n} "
                               f"in the project config (or both to null).")
    for n, gu, inv in plants:
        page_name = "GU Terminal" if len(plants) == 1 else f"GU Terminal {n}"
        pages.append((page_name, page(gu, labels[f"gu_busbar{n}"], inv,
                                      _FRT_INVERTER_CLASSES, labels[f"inverter{n}"])))
    return pages


def frt_raw_header(legend_name, variable):
    desc, unit = VARIABLE_LABELS.get(variable, (variable, ""))
    return f"{legend_name} {desc}" + (f" ({unit})" if unit else "")


# ==============================================================================
# 11b. Export one case
# ==============================================================================

def find_run_folder_any(app, name):
    """The IntFolder called `name` anywhere under Study Cases."""
    study = app.GetProjectFolder("study")
    found = [f for f in study.GetContents("*.IntFolder", 1) if f.loc_name == name]
    if not found:
        raise RuntimeError(f"Study case folder '{name}' not found under Study Cases.")
    if len(found) > 1:
        app.PrintWarn(f"  {len(found)} folders named '{name}' found — exporting "
                      f"the first one.")
    return found[0]


def result_column_index(elmRes):
    """{(LOC_NAME, class, variable): column} for every recorded variable."""
    index = {}
    for i in range(elmRes.GetNumberOfColumns()):
        obj, var = elmRes.GetObject(i), elmRes.GetVariable(i)
        if obj is not None and var:
            index.setdefault((obj.loc_name.upper(), obj.GetClassName(), var), i)
    return index


def export_frt_case(app, case, case_dir, pages):
    """
    Write one CSV per page for one study case.
    Returns (n_rows, t_end, files, missing) — n_rows 0 if there are no results.
    """
    case.Activate()
    elmRes = app.GetFromStudyCase("ComInc").p_resvar
    if elmRes is None:
        return 0, None, [], []

    elmRes.Load()
    try:
        n_rows = elmRes.GetNumberOfRows()
        if n_rows == 0:
            return 0, None, [], []
        index = result_column_index(elmRes)

        # time column once, shared by all pages of this case
        times = []
        for row in range(n_rows):
            err, t = elmRes.GetValue(row, -1)          # -1 = time column
            times.append(None if err else t)

        os.makedirs(case_dir, exist_ok=True)
        files, missing = [], []
        for page_name, curves in pages:
            cols, heads = [], []
            for elm, classes, legend, var in curves:
                col = next((index[(elm.upper(), cls, var)] for cls in classes
                            if (elm.upper(), cls, var) in index), None)
                if col is None:
                    missing.append(f"{page_name}: {elm} {var}")
                cols.append(col)
                heads.append(frt_raw_header(legend, var))

            fname = f"{page_name}.csv"
            with open(os.path.join(case_dir, fname), "w", newline="",
                      encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["time_s"] + heads)
                for row, t in enumerate(times):
                    if t is None:
                        continue
                    values = []
                    for col in cols:
                        if col is None:
                            values.append("")
                            continue
                        err, v = elmRes.GetValue(row, col)
                        values.append("" if err else repr(round(v, 10)))
                    writer.writerow([round(t, 6)] + values)
            files.append({"page": page_name, "csv_file": fname, "columns": heads})

        t_end = next((t for t in reversed(times) if t is not None), None)
        return n_rows, t_end, files, missing
    finally:
        elmRes.Release()


# ------------------------------------------------------------------------------
# 11c. Export a run (all cases, or a selection)
# ------------------------------------------------------------------------------
_FRT_CASE_RE = re.compile(r"FRT[ _]+(\d)PH[ _]+([0-9.]+)[ _]+(Q0|Qmax|Qmin)\s*$", re.I)


def frt_report_name(case_name):
    """'FRT 1PH 0.6 Qmax' -> '1P-60U-Qmax' (report name), or None."""
    m = _FRT_CASE_RE.search(case_name)
    if not m:
        return None
    n_ph, uret, q = m.groups()
    q = {"q0": "Q0", "qmax": "Qmax", "qmin": "Qmin"}[q.lower()]
    return f"{n_ph}P-{int(round(float(uret) * 100))}U-{q}"


def select_cases(cases, patterns):
    """
    The study cases matching any pattern: the PowerFactory name ('FRT 1PH 0.6
    Qmax') or the report name ('1P-60U-Qmax'), case-insensitive, * and ?
    allowed. Returns (selected cases, patterns that matched nothing).
    """
    chosen, unmatched = [], []
    for pat in patterns:
        p = str(pat).strip().lower()
        hits = [c for c in cases
                if fnmatch.fnmatchcase(c.loc_name.lower(), p)
                or fnmatch.fnmatchcase((frt_report_name(c.loc_name) or "").lower(), p)]
        if not hits:
            unmatched.append(pat)
        chosen += [c for c in hits if c not in chosen]
    return sorted(chosen, key=lambda c: c.loc_name), unmatched


def export_frt_raw(app, script_file, output_dir=None, case_patterns=None, run_folder=None):
    """
    Export FRT study cases to CSV: one folder per case, one CSV per plot page
    (POC.csv, GU Terminal.csv or GU Terminal 1/2.csv) + export_manifest.json.

    output_dir    None -> <paths.root>\\FRT Raw Data; else this folder. The run
                  folder name is always added: <output_dir>\\<run folder>\\<case>\\.
    case_patterns None -> every case of the run; else only the matching cases.
    run_folder    None -> paths.frt_run from the config, else the latest FRT_* run.
    Returns the output folder.
    """
    app.ClearOutputWindow()
    config_file = resolve_config_path(script_file)
    cfg     = load_yaml(config_file)
    pages   = frt_raw_pages(config_file)
    run     = run_folder or run_name_setting(cfg, "FRT")
    if run:
        folder = find_run_folder_any(app, run)
    else:
        folder = find_latest_run_folder(app.GetProjectFolder("study"), "FRT")
        if folder is None:
            raise RuntimeError("No FRT_* run folder in Study Cases. "
                               "Run frt_1_create_and_run_cases.py first.")
    if output_dir:
        out_dir = os.path.join(output_dir, safe_name(folder.loc_name))
        os.makedirs(out_dir, exist_ok=True)
    else:
        out_dir = results_dir(cfg, "frt_raw", folder.loc_name)
    all_cases = sorted(folder.GetContents("*.IntCase", 1), key=lambda c: c.loc_name)
    unmatched = []
    if case_patterns:
        cases, unmatched = select_cases(all_cases, case_patterns)
    else:
        cases = all_cases

    app.PrintPlain("=" * 72)
    app.PrintPlain("  GCC FRT Raw Data Export"
                   + (" (selected cases)" if case_patterns else ""))
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  Config            : {config_file}")
    app.PrintPlain(f"  Study case folder : {folder.loc_name}   ({len(all_cases)} cases)")
    if case_patterns:
        app.PrintPlain(f"  Selected          : {len(cases)} case(s) for "
                       + ", ".join(str(p) for p in case_patterns))
    app.PrintPlain(f"  Plots per case    : {', '.join(p for p, _ in pages)}")
    app.PrintPlain(f"  Output            : {out_dir}\n")
    for pat in unmatched:
        app.PrintWarn(f"  No case matches '{pat}'")
    if not cases:
        app.PrintError("Nothing to export: no case selected.")
        return out_dir

    entries, no_results, failed, all_missing = [], [], [], {}
    app.EchoOff()
    try:
        for case in cases:
            name = case.loc_name
            try:
                n_rows, t_end, files, missing = export_frt_case(
                    app, case, os.path.join(out_dir, name), pages)
            except Exception as exc:
                failed.append((name, str(exc)))
                continue
            if n_rows == 0:
                no_results.append(name)
                continue
            entries.append({"case": name, "folder": name, "n_rows": n_rows,
                            "t_end_s": round(t_end, 6) if t_end is not None else None,
                            "files": files, "missing_variables": missing})
            if missing:
                all_missing[name] = missing
            app.PrintPlain(f"  {name:28s} -> {len(files)} CSV(s), {n_rows} rows, "
                           f"t_end = {t_end:.3f} s"
                           + (f", {len(missing)} variable(s) missing" if missing else ""))
    finally:
        case_now = app.GetActiveStudyCase()
        if case_now is not None:
            case_now.Deactivate()
        app.EchoOn()

    # a selection adds to an existing folder: keep the cases exported earlier
    mpath = os.path.join(out_dir, "export_manifest.json")
    if case_patterns and os.path.isfile(mpath):
        try:
            with open(mpath, "r", encoding="utf-8") as f:
                old = json.load(f)
            done = {e["case"] for e in entries}
            entries = [e for e in old.get("cases", []) if e.get("case") not in done] + entries
            entries.sort(key=lambda e: e["case"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    manifest = {
        "study_case_folder":  folder.loc_name,
        "exported":           datetime.now().isoformat(timespec="seconds"),
        "project_config":     config_file,
        "output_dir":         out_dir,
        "selected_cases":     [str(p) for p in case_patterns] if case_patterns else None,
        "pages":              [p for p, _ in pages],
        "time_base_note":     "Time step may be non-uniform: interpolate on time_s.",
        "cases":              entries,
        "skipped_no_results": no_results,
        "failed":             [{"case": n, "error": e} for n, e in failed],
    }
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    app.PrintPlain("")
    app.PrintPlain("=" * 72)
    n_new = len(cases) - len(no_results) - len(failed)
    app.PrintPlain(f"  {n_new} case(s) exported to {out_dir}")
    if no_results:
        app.PrintPlain(f"  Skipped, no simulation results ({len(no_results)}): "
                       + ", ".join(no_results))
    for name, missing in all_missing.items():
        app.PrintWarn(f"  Not in the result file of {name} (left blank): "
                      + "; ".join(missing))
    for name, error in failed:
        app.PrintWarn(f"  FAILED  {name}: {error}")
    app.PrintPlain("=" * 72)
    return out_dir
