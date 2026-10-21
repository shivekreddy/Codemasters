# ==============================================================================
# GCC FRT Evaluation Script
# ==============================================================================
# Evaluates the FRT CSVs exported by gcc_export_frt.py. Runs OUTSIDE
# PowerFactory, in any Python 3 with PyYAML (matplotlib optional, for plots).
# It only reads the CSVs, so re-run it as often as you like.
#
# Input : <RAW_DATA_ROOT>\FRT Raw Data\   (RAW_DATA_ROOT is set in gcc_common.py)
# Output: <RAW_DATA_ROOT>\FRT Evaluation\
#           FRT_summary.csv   one row per case: verdict + value per check
#           plots\<case>.png  one figure per case (only if matplotlib exists)
#
# WHAT EACH CASE WAS (fault type, Uret, fault and clearance time, setpoints):
#   1. export_manifest.json, written by the simulation and export scripts
#   2. fallback: the CSV file name, e.g. FRT_1PH_0.05_Qmax
#        1PH  -> single-phase-to-ground fault
#        0.05 -> target Uret; clearance time from frt_scenarios in the grid code
#        Qmax -> pre-fault Q = +q_factor x Pmax (project config)
#      with the fault start (fault_start_s) and Pmax from the project config.
#   Both are always built; if the manifest disagrees with the file name and
#   grid code, the manifest is used and the difference is reported.
#
# CHECKS PER CASE (PASS / FAIL / N/A):
#   0. initial cond. : pre-fault P, Q, U match the case's setpoints
#   1. completed     : the simulation reached t_stop
#   2. connected     : the plant still exports active power at the end
#   3. P recovery    : P back to (100 - tolerance_percent) % of pre-fault P
#                      within recovery_time_s after clearance (grid code)
#   4. Iq injection  : additional reactive current during the fault
#                      >= k x voltage drop (project config)
#   Info only: achieved vs target retained voltage.
#   A check whose limit is null/missing in the YAML is reported as N/A.
#   Cases exported without results (zeros) are listed as NO DATA.
#
# SIGN: detected per case. Before the fault the plant exports Pmax, so if the
# recorded POC P is negative, P, Q and the currents are flipped. All results
# and plots are GENERATION POSITIVE.
#
# Usage (VS Code terminal):
#   python "Working Scripts\gcc_evaluate_frt.py"
#   python "Working Scripts\gcc_evaluate_frt.py" "<other raw data folder>"
# ==============================================================================

import os
import re
import sys
import csv
import json
import math

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcc_common as gcc    # RAW_DATA_ROOT, config lookup (no PowerFactory)


# ------------------------------------------------------------------------------
# SETTINGS
# ------------------------------------------------------------------------------
# Project config; None -> project_config(_update).yaml next to this script
CONFIG_FILE = None

# Raw data folder; None -> <RAW_DATA_ROOT>\FRT Raw Data
EXPORT_DIR = None

# Output folder; None -> <RAW_DATA_ROOT>\FRT Evaluation
OUTPUT_DIR = None

# Averaging window for pre-fault and end-of-run values [s]
PRE_WINDOW_S = 0.5

# Initial-condition tolerances (a sanity check on the setup, not a grid code
# limit): P and Q in pu of Pmax, U in pu
IC_TOL_PQ_PU = 0.02
IC_TOL_U_PU  = 0.02

# Placeholder (own choice, not from the grid code). None = not applied.
#   CONNECTED_MIN_P_PU : minimum P at end of run (pu of pre-fault P) to count
#                        as "still connected"
CONNECTED_MIN_P_PU = 0.05

# Additional reactive current, Netcode elektriciteit art. 3.19 sub 10
# (RfG compliance verification NL v2.0, section 4.2.18):
#   IQ_MAX_PU    : required injection is capped at 1.0 x I_N (sub 10k)
#   IQ_TOL_UPPER : band above the k-line, +10% (sub 10l)
#   IQ_TOL_LOWER : band below the k-line, -20% (sub 10l)
#   IQ_MIN_U_PU  : no injection required below 15% voltage (sub 10m);
#                  checked on the GU terminal voltage when it is recorded
IQ_MAX_PU    = 1.0
IQ_TOL_UPPER = 0.10
IQ_TOL_LOWER = 0.20
IQ_MIN_U_PU  = 0.15


# CSV file name pattern written by the export: FRT_<type>_<uret>_<Q label>
_NAME_RE = re.compile(r"^FRT_(3PH|2PH|1PH|2PHG)_([0-9.]+)_(Q0|Qmax|Qmin)$")
_FAULT_TYPES = {"3PH": "three_phase", "2PH": "two_phase",
                "1PH": "single_phase_to_ground", "2PHG": "two_phase_to_ground"}

COL_U1  = "POC Pos-Seq Voltage [pu]"
COL_U2  = "POC Neg-Seq Voltage [pu]"
COL_P   = "POC Active Power [MW]"
COL_Q   = "POC Reactive Power [Mvar]"
COL_IQ  = "POC Reactive Current [kA]"
COL_GU1 = "GU Pos-Seq Voltage [pu]"


# ==============================================================================
# 1.  LOADING
# ==============================================================================

def load_yaml(path):
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_case_csv(path):
    """Returns {header: [float, ...]}; blank cells become 0."""
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader)
        cols = {h: [] for h in headers}
        for row in reader:
            for h, v in zip(headers, row):
                cols[h].append(float(v) if v != "" else 0.0)
    return cols


# ==============================================================================
# 2.  WHAT EACH CASE WAS: file name + grid code, cross-checked with manifest
# ==============================================================================

def case_from_name(stem, frt_block, cfg):
    """
    Build the case definition from the file name, the grid code and the
    project config. Returns a dict, or None if the name doesn't match.
    """
    m = _NAME_RE.match(stem)
    if not m:
        return None
    ft_short, uret_str, q_label = m.groups()
    uret = float(uret_str)

    sim   = cfg.get("simulation", {})
    plant = cfg.get("plant", {})
    pmax  = float(plant.get("pmax_mw", 0.0))
    qmax  = float(plant.get("q_factor", 0.33)) * pmax
    t_fault = float(sim.get("fault_start_s", 10.0))

    t_clear = None
    for sc in frt_block.get("frt_scenarios") or []:
        if abs(float(sc["uret_pu"]) - uret) < 1e-6:
            t_clear = round(t_fault + float(sc["clearance_duration_s"]), 4)
            break

    return {
        "name":       stem.replace("_", " "),
        "fault_type": _FAULT_TYPES[ft_short],
        "uret":       uret,
        "q_label":    q_label,
        "t_fault":    t_fault,
        "t_clear":    t_clear,
        "t_stop":     float(sim["t_stop_s"]) if "t_stop_s" in sim else None,
        "psetp":      pmax,
        "qsetp":      {"Q0": 0.0, "Qmax": qmax, "Qmin": -qmax}[q_label],
        "usetp":      1.0,
    }


def resolve_case(stem, manifest_entry, frt_block, cfg):
    """
    Merge the manifest entry over the name-derived definition.
    Returns (case, source, warnings).
    """
    from_name = case_from_name(stem, frt_block, cfg)
    if manifest_entry is None and from_name is None:
        return None, None, [f"'{stem}' not in the manifest and the name "
                            f"does not follow FRT_<type>_<Uret>_<Q>"]
    if manifest_entry is None:
        warn = [] if from_name["t_clear"] is not None else \
            [f"Uret {from_name['uret']} not in frt_scenarios: no clearance time"]
        return from_name, "file name", warn

    case = dict(from_name or {})
    case.update({k: v for k, v in manifest_entry.items() if v is not None})
    warnings = []
    if from_name:
        checks = [("fault_type", 0), ("uret", 1e-6), ("t_fault", 1e-3),
                  ("t_clear", 1e-3), ("qsetp", 0.05), ("psetp", 0.05)]
        for key, tol in checks:
            a, b = manifest_entry.get(key), from_name.get(key)
            if a is None or b is None:
                continue
            differs = (a != b) if tol == 0 else abs(float(a) - float(b)) > tol
            if differs:
                warnings.append(f"{key}: manifest {a} vs file name/grid code "
                                f"{b} (manifest used)")
    return case, "manifest", warnings


# ==============================================================================
# 3.  TIME-SERIES HELPERS  (non-uniform time base: always work on time)
# ==============================================================================

def interp(t, y, tq):
    """Linear interpolation of y at time tq."""
    if tq <= t[0]:
        return y[0]
    if tq >= t[-1]:
        return y[-1]
    lo, hi = 0, len(t) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if t[mid] <= tq:
            lo = mid
        else:
            hi = mid
    if t[hi] == t[lo]:
        return y[hi]
    return y[lo] + (y[hi] - y[lo]) * (tq - t[lo]) / (t[hi] - t[lo])


def mean_between(t, y, t0, t1):
    """Time-weighted mean of y over [t0, t1] (trapezoidal)."""
    if t1 <= t0:
        return math.nan
    pts = [(t0, interp(t, y, t0))]
    pts += [(ti, yi) for ti, yi in zip(t, y) if t0 < ti < t1]
    pts.append((t1, interp(t, y, t1)))
    area = sum((pts[i + 1][0] - pts[i][0]) * (pts[i + 1][1] + pts[i][1]) / 2
               for i in range(len(pts) - 1))
    return area / (t1 - t0)


def settle_time_above(t, y, threshold, t_from):
    """First time >= t_from after which y stays >= threshold until the end."""
    t_settle = None
    for ti, yi in zip(t, y):
        if ti < t_from:
            continue
        if yi >= threshold:
            if t_settle is None:
                t_settle = ti
        else:
            t_settle = None
    return t_settle


# ==============================================================================
# 4.  CHECKS FOR ONE CASE
# ==============================================================================

def evaluate_case(case, cols, limits, in_ka, pmax):
    """Returns (result_dict, series_dict)."""
    t       = cols["time_s"]
    t_fault = float(case["t_fault"])
    t_clear = float(case["t_clear"])
    t_end   = t[-1]
    notes   = []

    u1 = cols[COL_U1]
    # Pre-fault window ends 10 ms before the fault: at t_fault the result file
    # holds both the pre- and post-event point.
    pre = lambda y: mean_between(t, y, t_fault - PRE_WINDOW_S - 0.01,
                                 t_fault - 0.01)

    # --- Sign: generation positive. Pre-fault the plant exports P > 0. -------
    sign = -1.0 if pre(cols[COL_P]) < 0 else 1.0
    if sign < 0:
        notes.append("POC P/Q/I recorded negative for export: sign flipped")
    p  = [sign * v for v in cols[COL_P]]
    q  = [sign * v for v in cols[COL_Q]]
    iq = [sign * v for v in cols[COL_IQ]]
    p_pre, q_pre, u_pre, iq_pre = pre(p), pre(q), pre(u1), pre(iq)

    r = {"case": case["name"], "source": case["_source"],
         "fault_type": case.get("fault_type"), "q_label": case.get("q_label"),
         "uret_target_pu": case.get("uret"),
         "t_fault_s": t_fault, "t_clear_s": t_clear,
         "p_pre_mw": round(p_pre, 3), "q_pre_mvar": round(q_pre, 3),
         "u_pre_pu": round(u_pre, 4)}

    # --- 0. initial conditions ------------------------------------------------
    ic_bad = []
    if case.get("psetp") is not None and abs(p_pre - case["psetp"]) > IC_TOL_PQ_PU * pmax:
        ic_bad.append(f"P {p_pre:.2f} vs {case['psetp']:.2f} MW")
    if case.get("qsetp") is not None and abs(q_pre - case["qsetp"]) > IC_TOL_PQ_PU * pmax:
        ic_bad.append(f"Q {q_pre:.2f} vs {case['qsetp']:.2f} Mvar")
    if case.get("usetp") is not None and abs(u_pre - case["usetp"]) > IC_TOL_U_PU:
        ic_bad.append(f"U {u_pre:.3f} vs {case['usetp']:.3f} pu")
    r["initial_conditions"] = "FAIL" if ic_bad else "PASS"
    if ic_bad:
        notes.append("pre-fault not at setpoint: " + ", ".join(ic_bad))

    # --- 1. completed ---------------------------------------------------------
    t_stop = case.get("t_stop")
    if case.get("complete") is not None:
        r["completed"] = "PASS" if case["complete"] else "FAIL"
    elif t_stop is not None:
        r["completed"] = "PASS" if t_end >= float(t_stop) - 0.02 else "FAIL"
    else:
        r["completed"] = "N/A"
    r["t_end_s"] = round(t_end, 3)

    # --- 2. connected ---------------------------------------------------------
    p_end = mean_between(t, p, t_end - PRE_WINDOW_S, t_end)
    r["p_end_mw"] = round(p_end, 3)
    if CONNECTED_MIN_P_PU is None or p_pre <= 0:
        r["connected"] = "N/A"
    else:
        r["connected"] = ("PASS" if p_end >= CONNECTED_MIN_P_PU * p_pre
                          else "FAIL")

    # --- 3. active power recovery ---------------------------------------------
    rec_time, rec_tol = limits["recovery_time_s"], limits["recovery_tol_pct"]
    if rec_time is None or rec_tol is None or p_pre <= 0:
        r["p_recovery"], r["p_recovery_time_s"] = "N/A", ""
    else:
        t_rec = settle_time_above(t, p, (1.0 - rec_tol / 100.0) * p_pre, t_clear)
        if t_rec is None:
            r["p_recovery"], r["p_recovery_time_s"] = "FAIL", "not recovered"
        else:
            dt = t_rec - t_clear
            r["p_recovery"] = "PASS" if dt <= rec_time else "FAIL"
            r["p_recovery_time_s"] = round(dt, 3)

    # --- 4. reactive current injection ----------------------------------------
    k, t_full = limits["k_factor"], limits["iq_full_s"]
    r["iq_required_pu"] = r["iq_achieved_pu"] = ""
    if k is None or t_full is None or not in_ka:
        r["iq_injection"] = "N/A"
    elif t_clear - t_fault <= t_full:
        r["iq_injection"] = "N/A"
        notes.append("fault shorter than the full Iq response time")
    else:
        # Evaluated after the settling time, until clearance
        w0, w1 = t_fault + t_full, t_clear

        # Residual voltage for the 15% threshold: GU terminals if recorded
        gu = cols.get(COL_GU1)
        gu_ok = gu is not None and COL_GU1 not in (case.get("missing_columns") or [])
        u_res = mean_between(t, gu if gu_ok else u1, w0, w1)
        r["u_residual_pu"] = round(u_res, 3)
        r["u_residual_at"] = "GU" if gu_ok else "POC"

        if u_res < IQ_MIN_U_PU:
            r["iq_injection"] = "N/A"
            notes.append(f"residual voltage {u_res:.2f} pu < {IQ_MIN_U_PU} pu at "
                         f"{r['u_residual_at']}: no reactive current required")
        else:
            k_line   = k * (u_pre - mean_between(t, u1, w0, w1))
            required = min(k_line, IQ_MAX_PU)
            lower    = (1.0 - IQ_TOL_LOWER) * required
            upper    = (1.0 + IQ_TOL_UPPER) * k_line
            achieved = (mean_between(t, iq, w0, w1) - iq_pre) / in_ka
            r["iq_required_pu"] = round(required, 3)
            r["iq_band_pu"]     = f"{lower:.3f} to {upper:.3f}"
            r["iq_achieved_pu"] = round(achieved, 3)
            r["iq_injection"]   = "PASS" if lower <= achieved <= upper else "FAIL"
            if k_line > IQ_MAX_PU:
                notes.append(f"k x dU = {k_line:.2f} pu, capped at "
                             f"{IQ_MAX_PU} x I_N")
        if case.get("fault_type") != "three_phase":
            notes.append("asymmetric fault: Iq checked on positive sequence only")

    # --- info: achieved retained voltage --------------------------------------
    r["uret_achieved_pu"] = round(mean_between(t, u1, t_fault + 0.02, t_clear), 3)

    if case.get("missing_columns"):
        notes.append("not recorded, exported as 0: "
                     + ", ".join(case["missing_columns"]))
    notes += case.get("_warnings", [])

    checks = [r["initial_conditions"], r["completed"], r["connected"],
              r["p_recovery"], r["iq_injection"]]
    r["overall"] = "FAIL" if "FAIL" in checks else "PASS"
    r["notes"] = "; ".join(notes)

    series = {"t": t, "u1": u1, "u2": cols.get(COL_U2), "p": p, "q": q,
              "iq": iq, "t_fault": t_fault, "t_clear": t_clear}
    return r, series


# ==============================================================================
# 5.  PLOTS (optional)
# ==============================================================================

def plot_case(series, result, limits, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t, tf, tc = series["t"], series["t_fault"], series["t_clear"]
    fig, axes = plt.subplots(3, 1, figsize=(10, 9), sharex=True)

    axes[0].plot(t, series["u1"], label="U1 POC")
    if series["u2"]:
        axes[0].plot(t, series["u2"], label="U2 POC")
    axes[0].set_ylabel("Voltage [pu]")

    axes[1].plot(t, series["p"], label="P POC (gen +)")
    axes[1].plot(t, series["q"], label="Q POC (gen +)")
    rec_time, rec_tol = limits["recovery_time_s"], limits["recovery_tol_pct"]
    if rec_time is not None and rec_tol is not None:
        thr = (1.0 - rec_tol / 100.0) * result["p_pre_mw"]
        axes[1].hlines(thr, tc, tc + rec_time, colors="k", linestyles=":",
                       label=f"P recovery limit ({100 - rec_tol:.0f} %)")
    axes[1].set_ylabel("Power [MW / Mvar]")

    axes[2].plot(t, series["iq"], label="Iq POC (gen +)")
    axes[2].set_ylabel("Current [kA]")
    axes[2].set_xlabel("Time [s]")

    for ax in axes:
        for x in (tf, tc):
            ax.axvline(x, color="grey", linestyle="--", linewidth=0.8)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="lower right", fontsize=8)
    axes[0].set_xlim(max(t[0], tf - 1.0),
                     min(t[-1], tc + (rec_time or 2.0) + 2.0))

    fig.suptitle(f"{result['case']}   overall: {result['overall']}   "
                 f"(P rec: {result['p_recovery']}, Iq: {result['iq_injection']})")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ==============================================================================
# 6.  MAIN
# ==============================================================================

def main():
    config_path = gcc.resolve_config_path(__file__, CONFIG_FILE)
    cfg = load_yaml(config_path)
    gc_path = cfg["grid_code"]["file"]
    if not os.path.isabs(gc_path):
        gc_path = os.path.join(os.path.dirname(config_path), gc_path)
    gc = load_yaml(gc_path)

    raw_dir = (sys.argv[1] if len(sys.argv) > 1 else
               EXPORT_DIR or gcc.raw_data_dir("FRT", create=False))
    out_dir = OUTPUT_DIR or os.path.join(gcc.RAW_DATA_ROOT, "FRT Evaluation")
    if not os.path.isdir(raw_dir):
        raise RuntimeError(f"Raw data folder not found: {raw_dir}. "
                           "Run gcc_export_frt.py in PowerFactory first.")
    os.makedirs(out_dir, exist_ok=True)

    # --- Manifest (optional) --------------------------------------------------
    manifest, by_file = {}, {}
    mpath = os.path.join(raw_dir, "export_manifest.json")
    if os.path.isfile(mpath):
        with open(mpath, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        by_file = {c["csv_file"]: c for c in manifest.get("cases", [])
                   if c.get("csv_file")}

    # --- Limits: grid code first, project config for project-defined values ---
    frt = gc.get("requirements_type_2", {}).get("fault_ride_through", {})
    rec = (frt.get("post_fault_recovery") or {}).get("active_power") or {}
    dvs = (cfg.get("grid_requirements_project_data") or {}) \
        .get("dynamic_voltage_support") or {}
    full_ms = (dvs.get("response_time_ms") or {}).get("full")
    limits = {
        "recovery_time_s":  rec.get("recovery_time_s"),
        "recovery_tol_pct": rec.get("tolerance_percent"),
        "k_factor":         dvs.get("k_factor"),
        "iq_full_s":        full_ms / 1000.0 if full_ms is not None else None,
    }
    plant = cfg.get("plant", {})
    pmax  = float(plant.get("pmax_mw", 0.0))
    in_ka = ((manifest.get("plant") or {}).get("in_ka")
             or (pmax / (math.sqrt(3) * float(plant["poc_voltage_kv"]))
                 if plant.get("poc_voltage_kv") else None))

    print("=" * 68)
    print("  GCC FRT Evaluation")
    print("=" * 68)
    print(f"Raw data  : {raw_dir}")
    print(f"Manifest  : {mpath if manifest else 'not found, using file names'}")
    print(f"Grid code : {gc_path}")
    print(f"Limits    : {limits}   In = {in_ka:.4f} kA" if in_ka else
          f"Limits    : {limits}")
    print(f"Output    : {out_dir}\n")

    plots_dir = os.path.join(out_dir, "plots")
    try:
        import matplotlib  # noqa: F401
        os.makedirs(plots_dir, exist_ok=True)
        make_plots = True
    except ImportError:
        make_plots = False
        print("matplotlib not installed: plots skipped "
              "(pip install matplotlib to enable)\n")

    results = []
    files = sorted(f for f in os.listdir(raw_dir)
                   if f.lower().endswith(".csv") and f.startswith("FRT_"))
    for fname in files:
        stem = fname[:-4]
        entry = by_file.get(fname)
        case, source, warnings = resolve_case(stem, entry, frt, cfg)
        if case is None:
            print(f"  {stem:24s}  SKIPPED  {warnings[0]}")
            continue
        case["_source"], case["_warnings"] = source, warnings

        cols = load_case_csv(os.path.join(raw_dir, fname))
        no_data = ((entry or {}).get("export_status") == "no_result_data"
                   or len(cols["time_s"]) < 2)
        if no_data or case.get("t_clear") is None:
            reason = ("no results (exported as zeros)" if no_data
                      else "no clearance time for this Uret")
            results.append({"case": case["name"], "source": source,
                            "overall": "NO DATA", "notes": reason})
            print(f"  {case['name']:24s}  NO DATA  {reason}")
            continue

        r, series = evaluate_case(case, cols, limits, in_ka, pmax)
        results.append(r)
        print(f"  {r['case']:24s}  {r['overall']:7s}  IC={r['initial_conditions']}"
              f"  done={r['completed']}  conn={r['connected']}"
              f"  P_rec={r['p_recovery']}  Iq={r['iq_injection']}"
              f"  Uret={r['uret_achieved_pu']} (target {r['uret_target_pu']})"
              f"  [{source}]")
        for w in warnings:
            print(f"      ! {w}")
        if make_plots:
            plot_case(series, r, limits, os.path.join(plots_dir, stem + ".png"))

    if not results:
        print("No FRT_*.csv files found to evaluate.")
        return

    fields = []
    for r in results:
        for k in r:
            if k not in fields:
                fields.append(k)
    out_csv = os.path.join(out_dir, "FRT_summary.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, restval="")
        writer.writeheader()
        writer.writerows(results)

    n = {v: sum(1 for r in results if r["overall"] == v)
         for v in ("PASS", "FAIL", "NO DATA")}
    print()
    print("=" * 68)
    print(f"  {n['PASS']} PASS, {n['FAIL']} FAIL, {n['NO DATA']} NO DATA "
          f"({len(results)} cases)")
    print(f"  Summary : {out_csv}")
    if make_plots:
        print(f"  Plots   : {plots_dir}")
    print("=" * 68)


if __name__ == "__main__":
    main()
