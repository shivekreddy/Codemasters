# ==============================================================================
# Inspect a hand-made Calculated Result (IntCalcres) and its data series
# ==============================================================================
# Read only: changes nothing. Prints every input attribute of
#   - the Calculated Result object(s) in one graph's data series, and
#   - the data series (PltDataseries) itself (curve table),
# so the automatic version of the sign fix can use PowerFactory's exact
# attribute names. Set CASE / RUN / PAGE / GRAPH to the graph you edited by
# hand, run it from PowerFactory and send the output window text (or save it
# with the button in the output window).
# ==============================================================================

import powerfactory

RUN   = "FRT_2026-11-11_13-56-23 (PPC Enabled)"
CASE  = "FRT 1PH 0.05 Qmin"
PAGE  = "POC"
GRAPH = "Curve plot(2)"


def dump(app, obj):
    cls = obj.GetClassName()
    app.PrintPlain(f"\n--- {obj.GetFullName()}   [{cls}]")
    try:
        names = app.GetAvailableAttributes(cls, "", 1, "e")
    except Exception as exc:
        app.PrintWarn(f"  GetAvailableAttributes failed: {exc}")
        return
    if isinstance(names, str):
        names = names.splitlines()
    for name in names:
        name = str(name).strip()
        if not name:
            continue
        try:
            val = obj.GetAttribute(name)
        except Exception as exc:
            val = f"<not readable: {exc}>"
        if hasattr(val, "GetFullName"):
            val = f"<{val.GetClassName()}> {val.GetFullName()}"
        elif isinstance(val, list):
            val = [f"<{v.GetClassName()}> {v.loc_name}" if hasattr(v, "GetFullName") else v
                   for v in val]
        app.PrintPlain(f"  {name:28s} = {val!r}"[:300])


def main():
    app = powerfactory.GetApplication()
    app.ClearOutputWindow()
    study = app.GetProjectFolder("study")
    runs = [f for f in study.GetContents("*.IntFolder", 1) if f.loc_name == RUN]
    if not runs:
        app.PrintError(f"Run folder '{RUN}' not found.")
        return
    cases = runs[0].GetContents(f"{CASE}.IntCase")
    if not cases:
        app.PrintError(f"Case '{CASE}' not found in '{RUN}'.")
        return
    cases[0].Activate()
    try:
        pages = app.GetGraphicsBoard().GetContents(f"{PAGE}.GrpPage")
        plots = pages[0].GetContents(f"{GRAPH}.PltLinebarplot") if pages else []
        if not plots:
            app.PrintError(f"No '{GRAPH}' on page '{PAGE}'.")
            return
        ds = plots[0].GetDataSeries()
        dump(app, ds)
        calcs = ds.GetIntCalcres() or []
        app.PrintPlain(f"\n{len(calcs)} Calculated Result object(s) in the data series")
        for c in calcs:
            dump(app, c)
            for child in c.GetContents() or []:
                dump(app, child)
    finally:
        cases[0].Deactivate()


main()
