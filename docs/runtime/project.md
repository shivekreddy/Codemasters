# Project

## Table of Contents
- [Overview](#overview)
- [Usage Examples](#usage-examples)
- [Class Description](#class-description)
- [Properties](#properties)
- [Methods](#methods)
- [Runtime State](#runtime-state)
- [Related Documentation](#related-documentation)

---

## Overview

`Project` is the **runtime representation of a GCC project**.

It wraps a validated `ProjectConfig`, loads the associated grid-code requirements, and provides:
- orchestration helpers
- derived accessors for frequently used project data
- runtime state for results and checks

`Project` intentionally contains **no configuration parsing logic**; it operates purely on validated configuration objects.

---

## Usage Examples

### Example 1 – Create a project from a loaded ProjectConfig
```python
cfg = ConfigLoader("./project_info.yaml").load()
project = Project(cfg)
```

### Example 2 – Access project metadata and paths
```python
project.project_name
project.project_number
project.requirements_path
```

### Example 3 – Iterate over enabled backends
```python
for backend in project.enabled_backends:
    run_backend(backend)
```

---

## Class Description

### Short description

Runtime wrapper around `ProjectConfig` with execution context.

### Technical description

`Project` acts as the **boundary between configuration and execution**.

On initialization it:
1. Stores the validated `ProjectConfig`.
2. Loads grid-code requirements using `ConfigLoader` and the configured requirements path.
3. Initializes runtime containers for simulation and check results.

All config-derived information is accessed via delegation to `ProjectConfig`.

---

## Properties

### Delegated project metadata

| Property | Type | Description |
|---------|------|-------------|
| `project_name` | `str \| None` | Human-readable project name |
| `project_number` | `str \| None` | Project identifier |
| `plant_type` | `PlantType` | Plant type of the project |

---

### Backend helpers

| Property / Method | Type | Description |
|------------------|------|-------------|
| `enabled_backends` | `List[BackendSpec]` | All backends with `enabled=True` |
| `get_backend(id)` | `BackendSpec` | Return backend by identifier |

---

### Paths and execution context

| Property | Type | Description |
|---------|------|-------------|
| `requirements_path` | `Path \| None` | Path to grid-code requirements YAML |
| `results_base_directory` | `Path \| None` | Base directory for results |
| `results_run_id` | `str \| None` | Identifier for the current run |

---

### Grid-code project data

| Property | Type | Description |
|---------|------|-------------|
| `grid_requirements_project_data` | `Dict[str, Any]` | Project-specific requirement overrides |

---

## Methods

### get_raw(path: str, default: Any = None) → Any

Access the **raw `project_info` dict** using a dotted path.

This method is intended as an **escape hatch** for exceptional cases where structured accessors are insufficient.

**Parameters**

- `path: str`  
  Dotted path into the raw configuration dictionary
- `default: Any`  
  Value returned if the path cannot be resolved

**Returns**

- `Any`

**Notes**

- No validation is performed
- Prefer structured accessors whenever possible

---

## Runtime State

The following attributes are **runtime-only** and are not part of the configuration model:

| Attribute | Type | Description |
|----------|------|-------------|
| `simulation_results` | `Dict[str, Any]` | Results of executed simulations |
| `check_results` | `Dict[str, Any]` | Results of compliance checks |

These containers are expected to be populated by downstream execution logic.

---

## Related Documentation

- project_config.md
- requirements_config.md
- config_loader.md
- backend_spec.md
