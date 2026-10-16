# ==============================================================================
# GCC FRT Evaluation Script
# ==============================================================================
# Evaluates the FRT results exported by gcc_export_results.py (STUDY = "FRT").
# Runs OUTSIDE PowerFactory, in any Python 3 with PyYAML (matplotlib optional,
# for plots). Re-run it as often as you like; it only reads the CSVs.
#
# Inputs:
#   * the export folder: {results.output_dir}\FRT_<timestamp>\
#       export_manifest.json + one CSV per study case
#   * the grid code YAML and project config (same files as the simulation
#     scripts), for the limits
#
# Checks per case (PASS / FAIL / N/A):
#   1. completed      : the simulation reached t_stop
#   2. connected      : the plant still exports active power at the end
#   3. P recovery     : P back to (100 - tolerance_percent) % of pre-fault P
#                       within recovery_time_s after fault clearance
#                       (grid code: fault_ride_through.post_fault_recovery)
#   4. Iq injection   : additional reactive current during the fault
#                       >= k x voltage drop (project config:
#                       dynamic_voltage_support)
#   Info only (no verdict): achieved retained voltage vs target.
#   A check whose limit is null/missing in the YAML is reported as N/A.
#
# Sign convention: the POC breaker records in LOAD convention (exporting P
# is negative). This script flips the POC quantities so that GENERATION IS
# POSITIVE in all results and plots.
#
# Outputs (written into the export folder):
#   frt_evaluation.csv     one row per case, one verdict + value per check
#   plots\<case>.png       one figure per case (only if matplotlib exists)
#
# Usage:
#   python gcc_evaluate_frt.py                -> latest FRT_* export folder
#   python gcc_evaluate_frt.py <export folder>
# ==============================================================================

import os
import sys
import csv
import json
import math

import yaml


# ------------------------------------------------------------------------------
# SETTINGS
# ------------------------------------------------------------------------------
# Project config; None -> project_config(_update).yaml next to this script
CONFIG_FILE = None

# Export folder to evaluate; None -> latest FRT_* folder in results.output_dir
# (a folder given on the command line overrides this)
EXPORT_DIR = None

# Sign applied to POC power/current to get generation-positive values
POC_SIGN = -1.0

# Averaging window for pre-fault and end-of-run values [s]
PRE_WINDOW_S = 0.5

# Placeholders, to be set from the Dutch grid code. None = not applied.
#   CONNECTED_MIN_P_PU : minimum P at end of run (pu of pre-fault P) to count
#                        as "still connected"
#   IQ_MAX_PU          : cap on the required additional reactive current
#                        (pu of rated current), e.g. converter current limit
#   IQ_TOLERANCE_PU    : allowed shortfall on the required reactive current
CONNECTED_MIN_P_PU = 0.05
IQ_MAX_PU          = None
IQ_TOLERANCE_PU    = 0.0


# ==============================================================================
# 1.  LOADING
# ==============================================================================

def _here():
    return os.path.dirname(os.path.abspath(__file__))


def load_yaml(path):
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_config_path():
    path = CONFIG_FILE or _here()
    if os.path.isdir(path):
        for name in ("project_config.yaml", "project_config_update.yaml"):
            if os.path.isfile(os.path.join(path, name)):
                return os.path.join(path, name)
    return path


def find_export_dir(out_dir):
    """Command-line folder, else EXPORT_DIR, else the latest FRT_* export."""
    if len(sys.argv) > 1:
        return sys.argv[1]
    if EXPORT_DIR:
        return EXPORT_DIR
    runs = sorted(
        d for d in os.listdir(out_dir)
        if d.startswith("FRT_")
        and os.path.isfile(os.path.join(out_dir, d, "export_manifest.json"))
    )
    if not runs:
        raise RuntimeError(f"No exported FRT_* folder found in {out_dir}. "
                           "Run gcc_export_results.py (STUDY='FRT') first.")
    return os.path.join(out_dir, runs[-1])   # ISO timestamps sort by date


def load_case_csv(path):
    """Returns {header: [float, ...]}; blank cells become NaN."""
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader)
        cols = {h: [] for h in headers}
        for row in reader:
            for h, v in zip(headers, row):
                cols[h].append(float(v) if v != "" else math.nan)
    return cols


# ==============================================================================
# 2.  TIME-SERIES HELPERS  (non-uniform time base: always work on time)
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
    """
    First time >= t_from after which y stays >= threshold until the end.
    None if it never settles above the threshold.
    """
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
# 3.  CHECKS FOR ONE CASE
# ==============================================================================

def evaluate_case(case, cols, limits, in_ka):
    """
    Returns (result_dict, series_dict). result_dict holds one verdict and value
    per check; series_dict holds the generation-positive signals for plotting.
    """
    t       = cols["time_s"]
    t_fault = float(case["t_fault"])
    t_clear = float(case["t_clear"])
    t_end   = t[-1]

    u1 = cols["POC Pos-Seq Voltage [pu]"]
    p  = [POC_SIGN * v for v in cols["POC Active Power [MW]"]]
    q  = [POC_SIGN * v for v in cols["POC Reactive Power [Mvar]"]]
    iq = [POC_SIGN * v for v in cols["POC Reactive Current [kA]"]]

    pre = lambda y: mean_between(t, y, t_fault - PRE_WINDOW_S, t_fault)
    p_pre, q_pre, u_pre, iq_pre = pre(p), pre(q), pre(u1), pre(iq)

    r = {"case": case["name"], "fault_type": case.get("fault_type"),
         "uret_target_pu": case.get("uret"), "q_label": case.get("q_label"),
         "p_pre_mw": round(p_pre, 3), "q_pre_mvar": round(q_pre, 3)}
    notes = []

    # Sign self-check: pre-fault Q and Iq should have the same sign
    if abs(q_pre) > 1.0 and q_pre * iq_pre < 0:
        notes.append("Iq and Q have opposite signs pre-fault: check the "
                     "reactive current sign convention")
    if p_pre <= 0:
        notes.append("pre-fault P is not positive after the sign flip: "
                     "check POC_SIGN")

    # --- 1. completed ---------------------------------------------------------
    r["completed"] = "PASS" if case.get("complete", True) else "FAIL"
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
        r["p_recovery"] = "N/A"
        r["p_recovery_time_s"] = ""
    else:
        threshold = (1.0 - rec_tol / 100.0) * p_pre
        t_rec = settle_time_above(t, p, threshold, t_clear)
        if t_rec is None:
            r["p_recovery"] = "FAIL"
            r["p_recovery_time_s"] = "not recovered"
        else:
            dt = t_rec - t_clear
            r["p_recovery"] = "PASS" if dt <= rec_time else "FAIL"
            r["p_recovery_time_s"] = round(dt, 3)

    # --- 4. reactive current injection ----------------------------------------
    k, t_full = limits["k_factor"], limits["iq_full_s"]
    if k is None or t_full is None or not in_ka:
        r["iq_injection"] = "N/A"
        r["iq_required_pu"] = r["iq_achieved_pu"] = ""
    elif t_clear - t_fault <= t_full:
        r["iq_injection"] = "N/A"
        r["iq_required_pu"] = r["iq_achieved_pu"] = ""
        notes.append("fault shorter than the full Iq response time")
    else:
        w0, w1 = t_fault + t_full, t_clear
        du = u_pre - mean_between(t, u1, w0, w1)
        required = k * du
        if IQ_MAX_PU is not None:
            required = min(required, IQ_MAX_PU)
        achieved = (mean_between(t, iq, w0, w1) - iq_pre) / in_ka
        r["iq_required_pu"] = round(required, 3)
        r["iq_achieved_pu"] = round(achieved, 3)
        r["iq_injection"] = ("PASS" if achieved >= required - IQ_TOLERANCE_PU
                             else "FAIL")
        if case.get("fault_type") != "three_phase":
            notes.append("asymmetric fault: Iq checked on positive sequence only")

    # --- info: achieved retained voltage --------------------------------------
    r["uret_achieved_pu"] = round(
        mean_between(t, u1, t_fault + 0.02, t_clear), 3)

    checks = [r["completed"], r["connected"], r["p_recovery"], r["iq_injection"]]
    r["overall"] = "FAIL" if "FAIL" in checks else "PASS"
    r["notes"] = "; ".join(notes)

    series = {"t": t, "u1": u1, "u2": cols.get("POC Neg-Seq Voltage [pu]"),
              "p": p, "q": q, "iq": iq, "t_fault": t_fault, "t_clear": t_clear}
    return r, series


# ==============================================================================
# 4.  PLOTS (optional)
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
# 5.  MAIN
# ==============================================================================

def main():
    config_path = resolve_config_path()
    cfg = load_yaml(config_path)
    gc_path = cfg["grid_code"]["file"]
    if not os.path.isabs(gc_path):
        gc_path = os.path.join(os.path.dirname(config_path), gc_path)
    gc = load_yaml(gc_path)

    export_dir = find_export_dir(cfg["results"]["output_dir"])
    with open(os.path.join(export_dir, "export_manifest.json"),
              "r", encoding="utf-8") as f:
        manifest = json.load(f)
    if manifest.get("study") != "FRT":
        raise RuntimeError(f"{export_dir} is not an FRT export.")

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
    in_ka = (manifest.get("plant") or {}).get("in_ka")

    print("=" * 68)
    print("  GCC FRT Evaluation")
    print("=" * 68)
    print(f"Export folder : {export_dir}")
    print(f"Grid code     : {gc_path}")
    print(f"Limits        : {limits}   In = {in_ka} kA")
    print()

    plots_dir = os.path.join(export_dir, "plots")
    try:
        import matplotlib  # noqa: F401
        os.makedirs(plots_dir, exist_ok=True)
        make_plots = True
    except ImportError:
        make_plots = False
        print("matplotlib not installed: plots skipped "
              "(pip install matplotlib to enable)\n")

    results = []
    for case in manifest["cases"]:
        if case.get("export_status") != "ok":
            print(f"  {case['name']:24s}  not evaluated "
                  f"({case.get('export_status')})")
            continue
        cols = load_case_csv(os.path.join(export_dir, case["csv_file"]))
        r, series = evaluate_case(case, cols, limits, in_ka)
        results.append(r)
        print(f"  {r['case']:24s}  {r['overall']:4s}   completed={r['completed']}"
              f"  connected={r['connected']}  P_rec={r['p_recovery']}"
              f"  Iq={r['iq_injection']}  Uret={r['uret_achieved_pu']}"
              f" (target {r['uret_target_pu']})")
        if make_plots:
            plot_case(series, r, limits, os.path.join(
                plots_dir, case["csv_file"].replace(".csv", ".png")))

    if not results:
        print("\nNo exported cases to evaluate.")
        return

    out_csv = os.path.join(export_dir, "frt_evaluation.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    n_fail = sum(1 for r in results if r["overall"] == "FAIL")
    print()
    print("=" * 68)
    print(f"  {len(results) - n_fail} / {len(results)} cases PASS")
    print(f"  Table : {out_csv}")
    if make_plots:
        print(f"  Plots : {plots_dir}")
    print("=" * 68)


if __name__ == "__main__":
    main()
