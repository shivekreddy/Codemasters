# BackendSpec

## Table of Contents
- [Overview](#overview)
- [Usage Examples](#usage-examples)
- [Class Description](#class-description)
- [Properties](#properties)
- [Related Documentation](#related-documentation)

---

## Overview

`BackendSpec` represents a **single simulation backend definition** as provided in the `project_info` configuration.
It is used to describe available tools (e.g. PowerFactory, PSCAD, PSS®E), their executables, versions, and backend‑specific settings.

Instances of this class are typically created indirectly while parsing a `ProjectConfig`.

---

## Usage Examples

### Example 1 – Access backend metadata
```python
backend.id
backend.name
backend.version
```

### Example 2 – Check whether a backend is enabled
```python
if backend.enabled:
    run_backend(backend)
```

---

## Class Description

### Short description

Typed container describing a single simulation backend.

### Technical description

`BackendSpec` is a lightweight `dataclass` without internal logic.
It holds backend identification, enable flags, optional paths to executables and configuration files, and an open‑ended `settings` dictionary for backend‑specific parameters.

The class itself performs no validation; correctness is expected to be ensured during configuration parsing.

---

## Properties

| Property | Type | Description |
|---------|------|-------------|
| `id` | `str` | Backend identifier (unique within project) |
| `name` | `BackendName` | Backend name enum |
| `enabled` | `bool` | Enable flag |
| `version` | `Optional[str]` | Backend version string |
| `executable` | `Optional[str]` | Path to backend executable |
| `simulation_setup_path` | `Optional[str]` | Path to simulation setup template |
| `element_mapper_path` | `Optional[str]` | Path to element mapper configuration |
| `settings` | `Dict[str, Any]` | Backend‑specific settings |

---

## Related Documentation

- [BaseConfig](base-config.md)
- [ProjectConfig](project-config.md)
- [RequirementsConfig](requirements-config.md)
- [ConfigLoader](config-loader.md)
- [TypeDefinitions](config-types.md)