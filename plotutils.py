import pandas as pd
import math
import cycler
import matplotlib

colors = [
    # according DNV Brandcentral
    # Primary colour palette
    "#0f204b",  # Dark blue
    "#009fda",  # Cyan
    "#003591",  # Sea blue
    "#99d9f0",  # Sky blue
    "#3f9c35",  # Land green
    # Secondary colour palette
    "#91ffb4",  # Digi green
    "#2b6173",  # Pine forest
    "#f2e6d5",  # Earth
    "#15c2bb",  # Eucalyptus
    "#fff377",  # Sunflower
    "#a1aae6",  # Lavender
    "#eb2a34",  # Energy red
    "#cccbc9",  # Sandstone
    "#b56700",  # Terracota
    "#988f86",  # Warm Grey
]


def apply_DNV_template():
    # Es ist vorgekommen, dass für einzelne Plottypes (scatter etc.) dann die Farben nicht genutzt wurden
    # und die Punkte in "weiß" dargestellt sind, wodurch sie nicht zu sehen sind. --> Klären
    matplotlib.rcParams["axes.prop_cycle"] = cycler.cycler("color", colors)
    dark_blue_font = "#0f204b"

    matplotlib.rcParams["patch.facecolor"] = "white"
    matplotlib.rcParams["font.family"] = ["Arial"]
    matplotlib.rcParams["font.weight"] = "bold"
    matplotlib.rcParams["axes.labelweight"] = "bold"
    matplotlib.rcParams["font.size"] = 10
    matplotlib.rcParams["text.color"] = dark_blue_font
    matplotlib.rcParams["axes.labelcolor"] = dark_blue_font
    matplotlib.rcParams["xtick.color"] = dark_blue_font
    matplotlib.rcParams["ytick.color"] = dark_blue_font


def calculate_rms(ull, period=0.02, refresh=2) -> list:
    """
    Calculate RMS Values from a given dataframe of instantaneous values.
    Parameters
    ----------
    ull : Dataframe
        Dataframe with the following order: [time, values, values, ...]
    period : float, optional
        Period in seconds over which to calculate the RMS.
        Set to 0 to automatically calculate period of a full sine wave.
    refresh : float, optional
        How many times the RMS should be refreshed per period.
    Returns
    -------
    res : list
        list of dataframes with the structure ['t', 'rms']
    """

    res = []
    for u in range(1, len(ull.columns)):
        if period == 0:
            startindex = get_zero_crossing(ull.iloc[:, u], 5)
            midindex = get_zero_crossing(ull.iloc[:, u], startindex + 5)
            endindex = get_zero_crossing(ull.iloc[:, u], midindex + 5)
        else:
            startindex = 0
            endindex = ull.loc[ull.iloc[:, 0] >= ull.iloc[0, 0] + period].index.values.astype(int)[0]
        sample_amount = endindex - startindex
        starttime = ull.iloc[startindex, 0]
        endtime = ull.iloc[endindex, 0]
        decimals = math.ceil(math.log10(1/(endtime - starttime)))
        phase_results = dict()
        while endindex + sample_amount < len(ull.iloc[:, u]):
            rms = math.sqrt((ull.iloc[startindex:endindex, u]**2).mean())
            starttime = ull.iloc[startindex, 0]
            endtime = ull.iloc[endindex, 0]
            time = round(endtime, decimals)
            phase_results[time] = rms
            startindex += int(round(sample_amount/refresh, 0))
            endindex += int(round(sample_amount/refresh, 0))
        u_rms = pd.DataFrame(phase_results.items(), columns=['t', 'rms'])
        res.append(u_rms)

    return res


def get_zero_crossing(df: pd.DataFrame, start: int) -> int:
    """
    return index of first zero crossing
    Parameters
    ----------
    df : Dataframe
        Frame with data points
    start : int
        Starting index

    Returns
    -------
    i: int
        Index of first encountered zero-crossing after start index
    """

    sig = -1
    if df.iloc[start] > 0:
        sig = 1
    for i in range(start, len(df)):
        if df.iloc[i] * sig < 0:
            return i
    print('Could not find zero crossing')
    exit()


def get_ylimits(dflist, padding: float, handle=None) -> tuple[float, float]:
    """
    Calculates Y-Axis limits for a given list of data points.

    Parameters
    ----------
    dflist : list
        List of Dataframes
    padding : float
        Padding factor based on max amplitude
    handle : string or list of strings, optional
        Optional handle(s) for the dataframe

    Returns
    -------
    resmin, resmax: tuple[float, float]
        Minimum and maximum setpoints for Y-Axis limits
    """
    resmin = 999999999
    resmax = -999999999
    for l in range(0, len(dflist)):
        if not handle:
            d = dflist[l]
        elif type(handle) == list:
            d = dflist[l][handle[l]]
        elif type(handle) == str:
            d = dflist[l][handle]
        thismin = d.min()
        thismax = d.max()
        if thismin < resmin:
            resmin = thismin
        if thismax > resmax:
            resmax = thismax
    resmin = resmin - padding * (resmax - resmin)
    resmax = resmax + padding * (resmax - resmin)
    return resmin, resmax


def get_steadystate(vals, buffersize: int = 100, deviationcriterium: float = 0.02) -> list:
    """
    Return steady-state values according to IEC 61000-4-30 based on deviation from average of last 50 cycles.
    Parameters
    ----------
    vals : list
        List of measurements
    buffersize : int, optional
        size of the buffer to calculate average from
    deviationcriterium : float, optional
        deviation from average needs to be smaller than this value to count as steady-state

    Returns
    -------
    list of steady-state values
    """
    pointer = buffersize
    while pointer < len(vals-1):
        start = pointer - buffersize
        end = pointer - 1
        m = sum(vals[start:end])/buffersize
        if abs(vals[pointer] - m) < deviationcriterium:
            return vals[pointer:]
        pointer += 1
    print('no steady state found, returning empty list')
    return []


def get_max_deviation(vals, ref: float = 1, min_and_max: bool = False):
    """
    return maximum deviation from reference
    Parameters
    ----------
    vals : list/iterable
        values to be compared, accepts combination of values, lists and Pandas Series (Dataframe Column)
    ref : float, optional
        reference to compare to. Default: 1
    min_and_max : Boolean, optional
        Return maximum positive and negative deviation if true, only max deviation if false. Default: False
    Returns
    -------
    Maximum deviation (positive or negative) or both
    """

    included_lists = filter(lambda x: isinstance(x, list), vals)
    included_series = filter(lambda x: isinstance(x, pd.Series), vals)
    allvals = list(filter(lambda x: not isinstance(x, (list, pd.Series)), vals))
    for vl in included_lists:
        allvals.extend(vl)
    for vd in included_series:
        allvals.extend(vd.tolist())
    dvals = [v - ref for v in allvals]
    minv = min(dvals)
    maxv = max(dvals)
    if min_and_max:
        return minv, maxv
    else:
        if abs(minv) > abs(maxv):
            return minv
        else:
            return maxv


def align_header_text(text, ref, fig, yfactor=0.75) -> None:
    """
    Align text box horizontally with a reference text box and display it above the reference.
    Parameters
    ----------
    text : Matplotlib Text box
        Text box that is to be moved
    ref : Matplotlib Text box
        Reference text box (e.g. subplot title)
    fig : Matplotlib figure
        Figure in which to display the text
    yfactor : float, optional
        How far above the text ist displayed (relative to ref height). 1 = borders are connecting

    Returns
    -------

    """
    refwidth = ref.get_window_extent().transformed(fig.transFigure.inverted())
    width = text.get_window_extent().transformed(fig.transFigure.inverted())
    xpos = refwidth.x0 + (refwidth.x1 - refwidth.x0) / 2 - (width.x1 - width.x0) / 2
    ypos = refwidth.y0 + yfactor * (refwidth.y1 - refwidth.y0)
    text.set_position((xpos, ypos))
