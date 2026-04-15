# ProjectConfig

## Table of Contents
- [Overview](#overview)
- [Usage Examples](#usage-examples)
- [Class Description](#class-description)
- [Class Structure](#class-structure)
- [Properties](#properties)
- [Methods](#methods)
- [Related Documentation](#related-documentation)

---

## Overview

`ProjectConfig` represents the **root configuration object** for a project.
It contains project metadata, plant classification, configuration paths, backend definitions, logging configuration, results handling and project-specific grid code bindings.

This class is registered under the config type **`project_info`** and is typically instantiated indirectly via configuration loading.

---

## Usage Examples

### Example 1 – Create from parsed configuration dictionary
```python
cfg = ProjectConfig.from_dict(config_data)
```

### Example 2 – Access project metadata
```python
cfg.project_number
cfg.project_name
```

### Example 3 – Get enabled simulation backends
```python
enabled = cfg.enabled_backends
```

---

## Class Description

### Short description

Container for all **project-level configuration data** and access helpers.

### Technical description

`ProjectConfig` is a `dataclass` extending `BaseConfig`.
It validates the configuration type, performs lightweight parsing of backend definitions, distinguishes known and unknown top-level keys, and provides strongly-typed accessors for commonly used configuration fields.

Backend definitions are parsed into `BackendSpec` objects.

---

## Properties

| Property | Type | Description |
|----------|------|-------------|
| meta | Dict[str, Any] | Project metadata (name, number, etc.) |
| plant | Dict[str, Any] | Plant classification and attributes |
| configs | Dict[str, Any] | References to external configuration files |
| backends | List[BackendSpec] | Defined simulation backends |
| results | Dict[str, Any] | Result storage configuration |
| logging_cfg | Dict[str, Any] | Logging configuration |
| grid_requirements_project_data | Dict[str, Any] | Project-specific grid code bindings |

### Derived / convenience properties

| Property | Type | Description |
|-------------|------|-------------|
| project_number | Optional[str] | Project identifier |
| project_name | Optional[str] | Human-readable project name |
| plant_type | PlantType | Plant type enum |
| requirements_path | Optional[Path] | Path to grid requirements file |
| grid_operator_overlays | List[Optional[Path]] | Overlay configuration paths |
| enabled_backends | List[BackendSpec] | Backends with enabled=True |
| results_base_directory | Optional[Path] | Base directory for results |
| results_run_id | Optional[str] | Result run identifier |
| log_level | Optional[str] | Logging level |
| log_file | Optional[Path] | Log file path |

---

## Methods

### from_dict(d: Dict[str, Any]) → ProjectConfig

Create a `ProjectConfig` instance from a configuration dictionary.

**Parameters**

- `d: Dict[str, Any]`  
  Parsed configuration mapping

**Returns**

- `ProjectConfig`  
  Parsed project configuration

**Errors**

- `ValueError` if `config_type` is not `project_info`
- `TypeError` if backend definitions are invalid

---

### get_backend(backend_id: str) → BackendSpec

Return backend configuration by ID.

**Parameters**

- `backend_id: str`  
  Backend identifier

**Returns**

- `BackendSpec`  
  Backend configuration

**Errors**

- `KeyError` if backend ID is not found

---

## Related Documentation

- [BaseConfig](base-config.md)
- [RequirementsConfig](requirements-config.md)
- [ConfigLoader](config-loader.md)
- [TypeDefinitions](config-types.md)
- [BackendSpec](backend-config.md)