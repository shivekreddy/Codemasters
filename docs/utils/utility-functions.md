# Utility Functions (PowerFactory Helpers)

## Table of Contents
- [Overview](#overview)
- [Usage Examples](#usage-examples)
- [Functions](#functions)
  - [get_PF](#get_pf)
  - [find_powerfactory_installations](#find_powerfactory_installations)
  - [get_nested_projects](#get_nested_projects)
  - [select_PF_project](#select_pf_project)
- [Internal Helper Functions](#internal-helper-functions)
- [Platform and Runtime Notes](#platform-and-runtime-notes)
- [Related Documentation](#related-documentation)

---

## Overview

This module contains **utility functions for interacting with DIgSILENT PowerFactory** from Python.

The functions cover:
- locating installed PowerFactory versions via the Windows registry
- dynamically attaching the correct PowerFactory Python API to `sys.path`
- instantiating a PowerFactory application instance
- basic GUI helpers for selecting PowerFactory installations and projects

The module is **Windows-specific** and assumes a local PowerFactory installation.

---

## Usage Examples

### Example 1 – Get newest PowerFactory instance (headless)
```python
pf = get_PF(headless=True, latest=True)
```

### Example 2 – Let user select installation and show GUI
```python
pf = get_PF(headless=False, latest=False)
```

### Example 3 – Select a PowerFactory project
```python
select_PF_project(pf)
```

---

## Functions

### get_PF(headless: bool, latest: bool)

Instantiate and return a PowerFactory application instance compatible with the current Python version.

**Behavior**

- Detects installed PowerFactory versions via the Windows registry
- Matches PowerFactory Python API to the running Python version
- Optionally selects the newest installation automatically
- Otherwise opens a Tk-based selection dialog

If successful, returns a valid PowerFactory application object.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `headless` | `bool` | If `True`, PowerFactory GUI is not shown |
| `latest` | `bool` | If `True`, automatically select newest installation |

**Returns**

- `pf` – PowerFactory application instance

**Notes**

- Modifies `sys.path` at runtime
- Prints status and errors to stdout
- Requires PowerFactory Python API

---

### find_powerfactory_installations()

Search the Windows registry for installed PowerFactory versions.

Registry locations searched include both 64-bit and 32-bit views.

**Returns**

- `List[Tuple[str, str]]` – list of `(version_name, install_path)`

**Notes**

- Requires read access to `HKEY_LOCAL_MACHINE`
- Silently ignores missing or malformed entries

---

### get_nested_projects(parent) → list

Recursively collect all PowerFactory projects (`IntPrj`) from the given parent folder.

**Parameters**

| Name | Description |
|------|-------------|
| `parent` | PowerFactory `IntFolder` object |

**Returns**

- `list` of PowerFactory `IntPrj` objects

---

### select_PF_project(pf)

Display a Tk-based list of accessible PowerFactory projects and activate the selected one.

**Parameters**

| Name | Description |
|------|-------------|
| `pf` | PowerFactory application instance |

**Returns**

- `None`

---

## Internal Helper Functions

The following functions are **internal GUI callbacks** and should not be used directly:

- `_button_activate_Project`
- `_button_select_installation`

They are used exclusively as Tk button callbacks.

---

## Platform and Runtime Notes

- Windows-only (uses `winreg`)
- Requires local PowerFactory installation
- Uses Tkinter for UI
- Modifies Python path dynamically
- Intended for interactive or engineering workflows, not CI/CD

---

## Related Documentation

- project.md
- backend_spec.md
- logging.md
