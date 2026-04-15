# Plot Utilities

## Table of Contents
- [Overview](#overview)
- [Usage Examples](#usage-examples)
- [Functions](#functions)
  - [apply_DNV_template](#apply_dnv_template)
  - [calculate_rms](#calculate_rms)
  - [get_zero_crossing](#get_zero_crossing)
  - [get_ylimits](#get_ylimits)
  - [get_steadystate](#get_steadystate)
  - [get_max_deviation](#get_max_deviation)
  - [align_header_text](#align_header_text)
- [Constants](#constants)
- [Platform and Runtime Notes](#platform-and-runtime-notes)
- [Related Documentation](#related-documentation)

---

## Overview

This module provides **plotting and signal‑processing helper functions** used in grid‑code analysis and result visualization.

The focus is on:
- consistent DNV‑style plotting (Matplotlib configuration)
- post‑processing of simulation signals (RMS, steady‑state, deviation)
- small layout helpers for figures

All functions are **stateless utilities** and do not depend on project configuration.

---

## Usage Examples

### Example 1 – Apply DNV plotting style
```python
apply_DNV_template()
```

### Example 2 – Calculate RMS values
```python
rms_data = calculate_rms(df, period=0.02, refresh=2)
```

### Example 3 – Determine y‑axis limits
```python
ymin, ymax = get_ylimits(dataframes, padding=0.1, handle="U")
```

---

## Functions

### apply_DNV_template() → None

Apply a **DNV‑compliant Matplotlib style** globally.

**Behavior**
- Sets the Matplotlib color cycle to the DNV color palette
- Configures font family, font weight, font size
- Sets axis, tick, and text colors

**Side effects**
- Modifies global `matplotlib.rcParams`
- Affects all subsequent plots in the Python process

---

### calculate_rms(ull, period: float = 0.02, refresh: int = 2) → list

Calculate RMS values from instantaneous time‑domain signals.

**Parameters**

| Name | Type | Description |
|-----|------|-------------|
| `ull` | `pd.DataFrame` | DataFrame with columns `[time, signal1, signal2, ...]` |
| `period` | `float` | RMS window length in seconds; `0` triggers automatic period detection |
| `refresh` | `int` | Number of RMS updates per RMS window |

**Returns**

| Type | Description |
|------|-------------|
| `list[pd.DataFrame]` | One DataFrame per signal, each with columns `['t', 'rms']` |

**Notes**
- Automatic period detection uses zero‑crossings
- Output sampling depends on `refresh`

---

### get_zero_crossing(df: pd.Series, start: int) → int

Return the index of the first zero crossing after a given start index.

**Parameters**

| Name | Type | Description |
|-----|------|-------------|
| `df` | `pd.Series` | Signal values |
| `start` | `int` | Index at which to start searching |

**Returns**

| Type | Description |
|------|-------------|
| `int` | Index of first detected zero crossing |

**Behavior**
- Searches for a sign change in the signal
- Terminates the program if no zero crossing is found

---

### get_ylimits(dflist, padding: float, handle = None) → tuple[float, float]

Calculate y‑axis limits for one or multiple datasets with symmetric padding.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `dflist` | `list` | List of Pandas DataFrames |
| `padding` | `float` | Padding factor relative to total amplitude |
| `handle` | `str | list | None` | Column name(s) to extract from DataFrames |

**Returns**

| Type | Description |
|------|-------------|
| `tuple[float, float]` | Minimum and maximum y‑axis limits |

---

### get_steadystate(vals, buffersize: int = 100, deviationcriterium: float = 0.02) → list

Determine steady‑state values according to IEC 61000‑4‑30 logic.

A value is considered steady‑state if its deviation from the running mean of the buffer is below the given threshold.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `vals` | `list` | Sequence of values |
| `buffersize` | `int` | Window size for averaging |
| `deviationcriterium` | `float` | Maximum allowed deviation from mean |

**Returns**

| Type | Description |
|------|-------------|
| `list` | Portion of `vals` identified as steady‑state |

---

### get_max_deviation(vals, ref: float = 1, min_and_max: bool = False)

Return the maximum deviation from a reference value.

Supports mixed input:
- scalar values
- lists
- Pandas Series

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `vals` | `iterable` | Values to compare against reference |
| `ref` | `float` | Reference value |
| `min_and_max` | `bool` | If `True`, return `(min, max)` deviations |

**Returns**

| Type | Description |
|------|-------------|
| `float` or `tuple[float, float]` | Maximum deviation or `(min, max)` |

---

### align_header_text(text, ref, fig, yfactor: float = 0.75) → None

Align a Matplotlib text element horizontally above a reference text element.

Used to fine‑tune figure layout, e.g. placing a header centered above a subplot title.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `text` | `matplotlib.text.Text` | Text element to move |
| `ref` | `matplotlib.text.Text` | Reference text element |
| `fig` | `matplotlib.figure.Figure` | Figure containing the text |
| `yfactor` | `float` | Vertical offset scaling relative to reference height |

---

## Constants

### colors

A fixed color palette according to **DNV Brandcentral**, used as the Matplotlib color cycle.

---

## Platform and Runtime Notes

- Depends on `matplotlib`, `pandas`, `cycler`
- Modifies global Matplotlib state (`rcParams`)
- Intended for interactive analysis and reporting

---

## Related Documentation

- func_lib.md
- project.md
- requirements_config.md
