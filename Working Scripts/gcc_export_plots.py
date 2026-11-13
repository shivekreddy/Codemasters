# ==============================================================================
# GCC Export Plots (FRT and Frequency)
# ==============================================================================
# Writes the plot pages of the LATEST FRT_* and/or FREQ_* run folder in Study
# Cases as image files:
#
#   <DIRECTORY>\PowerFactory Plots\
#       FRT\<FRT run folder>\<study case>\POC.png
#                                         \GU Terminal 1.png ...
#       Frequency\<FREQ run folder>\<study case> - Plot 1 - Main Results.png ...
#
#   e.g. ...\PowerFactory Plots\FRT\FRT_2026-11-11_13-56-23 (PPC Enabled)\
#            FRT 3PH 0.05 Q0\POC.png
#        ...\PowerFactory Plots\Frequency\FREQ_2026-11-12_16-50-24\
#            LFSMO Step - Plot 2 - Active power with labels.png
#
# Folders are created when missing; existing files are overwritten.
# Cases without simulation results are skipped (listed at the end).
# Stand-alone: no other script is needed.
#
# Usage: set the SETTINGS below, point a ComPython object at this script and
# run it from PowerFactory after the simulations have finished.
# ==============================================================================

import os
import re
import fnmatch
from datetime import datetime

import powerfactory


# ------------------------------------------------------------------------------
# SETTINGS
# ------------------------------------------------------------------------------
# "PowerFactory Plots" is created inside this folder (the folder must exist)
DIRECTORY = r"C:\Users\SHIRED\Downloads\Results Export"

FRT       = 1    # 1 = export the FRT plots, 0 = skip
FREQUENCY = 0    # 1 = export the frequency plots, 0 = skip

# Image format of PowerFactory's "Write graphic" command (ComWr):
#   "png" (default), "pdf", "svg", "emf", "wmf", "bmp"
FORMAT = "png"


# ==============================================================================

# Per study: run-folder prefix, output subfolder, plot pages to export (as
# created by gcc_frt_script.py / gcc_freq_script.py; * = wildcard), and
# whether each study case gets its own folder.
STUDIES = {
    "FRT": {
        "prefix": "FRT", "subfolder": "FRT",
        "pages": ["POC", "GU Terminal*"],
        "folder_per_case": True,
    },
    "Frequency": {
        "prefix": "FREQ", "subfolder": "Frequency",
        "pages": ["* Plot 1 - *", "* Plot 2 - *", "* Plot 3 - *"],
        "folder_per_case": False,
    },
}

# 'FRT_2026-11-11_13-56-23', also with text added after it when the folder
# was renamed by hand, e.g. 'FRT_2026-11-11_13-56-23 (PPC Enabled)'.
_TAG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}|"
                     r"\d{2}-\d{2}-\d{4}_\d{2}-\d{2}-\d{2})")
_TAG_FORMATS = ("%Y-%m-%d_%H-%M-%S", "%d-%m-%Y_%H-%M-%S")   # new and old style
_BAD_CHARS = re.compile(r'[<>:"/\\|?*]')                     # not allowed in Windows names


def run_time(folder, prefix):
    """Timestamp of a '<prefix>_<date>_<time>[ anything]' folder, or None."""
    name = folder.loc_name
    if not name.startswith(prefix + "_"):
        return None
    m = _TAG_RE.match(name[len(prefix) + 1:])
    if not m:
        return None
    for fmt in _TAG_FORMATS:
        try:
            return datetime.strptime(m.group(1), fmt)
        except ValueError:
            pass
    return None


def latest_run_folder(app, prefix):
    """The latest '<prefix>_*' run folder in Study Cases (by its timestamp)."""
    study = app.GetProjectFolder("study")
    if study is None:
        raise RuntimeError("No Study Cases folder: activate the project first.")
    runs = [(t, f) for f in study.GetContents(f"{prefix}_*.IntFolder")
            if (t := run_time(f, prefix)) is not None]
    if not runs:
        return None
    return max(runs, key=lambda r: r[0])[1]


def safe(name):
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


def export_page(app, page, path):
    """Show the page and write it to path with ComWr (overwrites). Error or None."""
    app.GetGraphicsBoard().Show(page)
    if os.path.isfile(path):
        os.remove(path)                       # overwrite, and a stale file can't fake success
    comWr = app.GetFromStudyCase("ComWr")
    comWr.iopt_rd    = FORMAT
    comWr.iopt_savas = 0                      # write to f without a dialog
    comWr.f          = path
    if comWr.Execute():
        return "export command failed"
    if not os.path.isfile(path):
        return "no file written (try another FORMAT, e.g. 'pdf' or 'wmf')"
    return None


def export_study(app, label, spec, root):
    """Export every case of the latest run folder of one study. Returns a summary."""
    app.PrintPlain(f"\n--- {label} " + "-" * (66 - len(label)))
    run_folder = latest_run_folder(app, spec["prefix"])
    if run_folder is None:
        app.PrintWarn(f"  No {spec['prefix']}_* run folder in Study Cases: nothing exported.")
        return {"label": label, "files": 0, "out": None, "no_results": [], "failed": []}

    out_dir = os.path.join(root, spec["subfolder"], safe(run_folder.loc_name))
    os.makedirs(out_dir, exist_ok=True)
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
                case_dir = os.path.join(out_dir, safe(name))
                os.makedirs(case_dir, exist_ok=True)
                path = os.path.join(case_dir, f"{safe(page.loc_name)}.{FORMAT}")
            else:
                # 'LFSMO Plot 1 - Main Results' -> 'LFSMO Step - Plot 1 - Main Results'
                short = re.sub(r"^.*?(?=Plot \d)", "", page.loc_name) or page.loc_name
                path = os.path.join(out_dir, f"{safe(name)} - {safe(short)}.{FORMAT}")
            error = export_page(app, page, path)
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


def main():
    app = powerfactory.GetApplication()
    app.ClearOutputWindow()

    app.PrintPlain("=" * 72)
    app.PrintPlain("  GCC Export Plots")
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  Directory : {DIRECTORY}")
    app.PrintPlain(f"  FRT       : {FRT}")
    app.PrintPlain(f"  Frequency : {FREQUENCY}")
    app.PrintPlain(f"  Format    : {FORMAT}")

    if not os.path.isdir(DIRECTORY):
        app.PrintError(f"Directory not found:\n  {DIRECTORY}\n"
                       "Create it, or set DIRECTORY at the top of gcc_export_plots.py.")
        return
    selected = [(label, spec) for label, spec, on in
                (("FRT", STUDIES["FRT"], FRT), ("Frequency", STUDIES["Frequency"], FREQUENCY))
                if on]
    if not selected:
        app.PrintWarn("FRT and Frequency are both 0: nothing to export.")
        return

    root = os.path.join(DIRECTORY, "PowerFactory Plots")
    os.makedirs(root, exist_ok=True)

    summaries = []
    for label, spec in selected:
        try:
            summaries.append(export_study(app, label, spec, root))
        except Exception as exc:            # one study never stops the other
            app.PrintError(f"  {label} export aborted: {exc}")

    app.PrintPlain("")
    app.PrintPlain("=" * 72)
    app.PrintPlain("  SUMMARY")
    for s in summaries:
        if s["out"] is None:
            app.PrintPlain(f"  {s['label']:9s}: no run folder found")
            continue
        app.PrintPlain(f"  {s['label']:9s}: {s['files']} plot(s) -> {s['out']}")
        if s["no_results"]:
            app.PrintPlain(f"             skipped, no simulation results ({len(s['no_results'])}): "
                           + ", ".join(s["no_results"]))
        for what, error in s["failed"]:
            app.PrintWarn(f"             FAILED  {what}: {error}")
    app.PrintPlain("=" * 72)


main()
