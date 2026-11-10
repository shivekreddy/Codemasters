# ==============================================================================
# GCC FRT — Export Plots
# ==============================================================================
# Exports the FRT plot pages of every study case in the latest FRT_* run
# folder as image files:
#   POC, GU Terminal (one plant) or GU Terminal 1 + GU Terminal 2 (two plants)
#
# Output:  <PLOT_DIR>\<run folder>\<case>_<page>.<format>
#   e.g.   ...\FRT Plots\FRT_2026-11-10_09-12-44\FRT_3PH_0.05_Q0_POC.png
#
# Cases without simulation results are skipped (listed at the end), so only
# simulated cases are exported. Stand-alone: no other script is needed.
#
# Usage: point a ComPython object at this script and run it from PowerFactory
# after the simulations have finished.
# ==============================================================================

import os
import fnmatch
from datetime import datetime

import powerfactory


# ------------------------------------------------------------------------------
# SETTINGS
# ------------------------------------------------------------------------------
# Folder the plots are written to (a subfolder per run folder is created)
PLOT_DIR = r"C:\Users\SHIRED\Downloads\Results Export\FRT Plots"

# Image format of PowerFactory's "Write graphic" command (ComWr):
#   "png", "pdf", "svg", "emf", "wmf", "bmp"
FORMAT = "png"

# Plot pages to export (names as created by gcc_frt_script.py; * = wildcard)
PAGES = ["POC", "GU Terminal*"]

# Run folder. None = the latest FRT_* folder in Study Cases.
RUN_TAG = None


# ==============================================================================

_TAG_FORMATS = ("%Y-%m-%d_%H-%M-%S", "%d-%m-%Y_%H-%M-%S")   # new and old style


def _run_time(folder):
    """Timestamp of an 'FRT_<date>_<time>' folder, or None."""
    stem = folder.loc_name[len("FRT_"):]
    for fmt in _TAG_FORMATS:
        try:
            return datetime.strptime(stem, fmt)
        except ValueError:
            pass
    return None


def find_run_folder(app):
    """The run folder named by RUN_TAG, or the latest FRT_* folder."""
    study = app.GetProjectFolder("study")
    if RUN_TAG:
        found = study.GetContents(f"{RUN_TAG}.IntFolder")
        if not found:
            raise RuntimeError(f"Run folder '{RUN_TAG}' not found in Study Cases.")
        return found[0]
    runs = [(t, f) for f in study.GetContents("FRT_*.IntFolder")
            if (t := _run_time(f)) is not None]
    if not runs:
        raise RuntimeError("No FRT_* run folder found in Study Cases. "
                           "Run gcc_frt_script.py first.")
    return max(runs, key=lambda r: r[0])[1]


def has_results(app):
    """True if the active case's result file holds simulation data."""
    elmRes = app.GetFromStudyCase("ComInc").p_resvar
    if elmRes is None:
        return False
    elmRes.Load()
    try:
        return elmRes.GetNumberOfRows() > 0
    finally:
        elmRes.Release()


def plot_pages(app):
    """The active case's plot pages matching PAGES, POC first."""
    pages = [p for p in app.GetGraphicsBoard().GetContents("*.GrpPage")
             if any(fnmatch.fnmatch(p.loc_name, pat) for pat in PAGES)]
    return sorted(pages, key=lambda p: (p.loc_name != "POC", p.loc_name))


def export_page(app, page, path):
    """Show the page and write it to path with ComWr. Returns an error or None."""
    app.GetGraphicsBoard().Show(page)
    comWr = app.GetFromStudyCase("ComWr")
    comWr.iopt_rd    = FORMAT
    comWr.iopt_savas = 0                  # write to f without a dialog
    comWr.f          = path
    if comWr.Execute():
        return "export command failed"
    if not os.path.isfile(path):
        return f"no file written (try another FORMAT, e.g. 'wmf' or 'pdf')"
    return None


def main():
    app = powerfactory.GetApplication()
    app.ClearOutputWindow()

    run_folder = find_run_folder(app)
    out_dir    = os.path.join(PLOT_DIR, run_folder.loc_name)
    os.makedirs(out_dir, exist_ok=True)
    cases = sorted(run_folder.GetContents("*.IntCase"), key=lambda c: c.loc_name)

    app.PrintPlain("=" * 72)
    app.PrintPlain("  GCC FRT — Export Plots")
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  Run folder : {run_folder.loc_name}   ({len(cases)} cases)")
    app.PrintPlain(f"  Output     : {out_dir}\n")

    n_files, no_results, failed = 0, [], []
    for case in cases:
        name = case.loc_name
        case.Activate()
        if not has_results(app):
            no_results.append(name)
            continue

        pages = plot_pages(app)
        if not pages:
            failed.append((name, "no POC / GU Terminal plot pages"))
            continue

        written = []
        for page in pages:
            fname = f"{name}_{page.loc_name}".replace(" ", "_") + f".{FORMAT}"
            error = export_page(app, page, os.path.join(out_dir, fname))
            if error:
                failed.append((f"{name} / {page.loc_name}", error))
            else:
                written.append(page.loc_name)
                n_files += 1
        if written:
            app.PrintPlain(f"  {name:24s} -> {', '.join(written)}")

    case_now = app.GetActiveStudyCase()
    if case_now is not None:
        case_now.Deactivate()

    app.PrintPlain("")
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  {n_files} plot(s) written to {out_dir}")
    if no_results:
        app.PrintPlain(f"  Skipped, no simulation results ({len(no_results)}): "
                       + ", ".join(no_results))
    for what, error in failed:
        app.PrintWarn(f"  FAILED  {what}: {error}")
    app.PrintPlain("=" * 72)


main()
