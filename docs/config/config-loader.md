# ConfigLoader

## Table of Contents
- [Overview](#overview)
- [Usage Examples](#usage-examples)
- [Class Description](#class-description)
- [Properties](#properties)
- [Methods](#methods)
- [Template Mapping](#template-mapping)
- [Related Documentation](#related-documentation)

---

## Overview

`ConfigLoader` loads YAML configuration files, validates their structure against a template, and returns the correct `BaseConfig` subclass based on the `config_type` field.

The loader relies on the **configuration registry** (`_CONFIG_REGISTRY`) to map `config_type` → config class (registered via `@register_config`).

---

## Usage Examples

### Example 1 – Load a project config
```python
loader = ConfigLoader("./project_info.yaml", plant_type="type_2")
project_cfg = loader.load()
```

### Example 2 – Lazy access via `.config`
```python
loader = ConfigLoader("./grid_code_requirements.yaml", plant_type="type_1")
req_cfg = loader.config
```

### Example 3 – Validate only (collect warnings)
```python
loader = ConfigLoader("./project_info.yaml")
data = yaml.safe_load(Path("./project_info.yaml").read_text(encoding="utf-8"))
warnings = loader.validate(data)
```

---

## Class Description

### Short description

YAML config loader with template-based structural checks and registry-based class selection.

### Technical description

`ConfigLoader` performs the following steps when calling `load()`:

1. Read YAML via `yaml.safe_load`.
2. Ensure `config_type` exists.
3. Validate structure against the configured template for that `config_type`.
4. Look up the corresponding config class in `_CONFIG_REGISTRY`.
5. Instantiate the config class by calling `cls.from_dict(...)`.
   - If the `from_dict` signature contains `plant_type`, the loader passes it automatically.

Validation returns a list of warnings. The current implementation **does not abort on warnings**.

---

## Properties

| Property | Type | Description |
|---------|------|-------------|
| `config_path` | `Path` | Path of the YAML configuration file |
| `plant_type` | `PlantType` | Plant type passed into configs that require it |
| `_config` | `BaseConfig \| None` | Cached loaded configuration instance |
| `config_type` | `str \| None` | Loaded `config_type` from YAML |

---

## Methods

### __init__(config_path: str | Path, plant_type: PlantType = 'type_2')

Create a loader for a given YAML config file.

**Parameters**

- `config_path: str | Path`  
  Path to the YAML configuration file
- `plant_type: PlantType`  
  Plant type (`type_1` or `type_2`) used when instantiating plant-type-aware configs

---

### load() → BaseConfig

Load YAML, validate structure, and return the appropriate `BaseConfig` subclass.

**Returns**

- `BaseConfig`  
  Concrete config instance selected by `config_type`

**Errors**

- `FileNotFoundError` if the YAML file does not exist
- `yaml.YAMLError` if YAML parsing fails
- `ValueError` if the configuration is empty, missing `config_type`, unsupported by template mapping, or not registered in `_CONFIG_REGISTRY`

---

### config (property) → BaseConfig

Lazy accessor: loads the config on first access and returns the cached instance.

---

### validate(config: dict) → list[str]

Validate the configuration structure against its template and run type-specific checks.

**Returns**

- `list[str]`  
  Warning messages (empty if no issues)

**Notes**

- Template is selected from `config_template` by `self.config_type`.
- Structural validation is performed by `check_structure(...)`.
- Additional checks are dispatched by `config_type`:
  - `project_info` → `validate_project_info(...)`
  - `grid_code_requirements` → `validate_grid_code_requirements(...)` (currently `pass`)

---

### check_structure(schema: Any, data: Any, path: str, warnings: list[str]) → None

Recursively compare YAML data against the template structure.

**Behavior**

- Adds warnings for:
  - missing keys in the config (`Missing key: ...`)
  - type mismatches (dict vs list vs scalar)
- Logs extra keys found in config but not present in schema (informational), but does **not** add them to `warnings`.

---

### validate_project_info(config: dict, warnings: list[str]) → None

Project-info specific validation checks, appended as warnings:

- Enabled backends must define `simulation_setup_path`.
- Grid-operator overlays must not have `path: null`.
- Project-specific requirement data (`grid_requirements_project_data`) should not be empty and should not contain null fields.

---

### validate_grid_code_requirements(config: dict, warnings: list[str]) → None

Placeholder for grid-code-requirements specific checks (currently not implemented).

---

## Template Mapping

The loader uses a static mapping of `config_type` to a template YAML file:

- `grid_code_requirements` → `./config/master_templates/grid-code-requirements_template.yaml`
- `project_info` → `./config/master_templates/project_info_template.yaml`

If a `config_type` is not present in this mapping, `load()` raises `ValueError`.

---

## Related Documentation

- [BaseConfig](base-config.md)
- [ProjectConfig](project-config.md)
- [RequirementsConfig](requirements-config.md)
- [TypeDefinitions](config-types.md)
- [BackendSpec](backend-config.md)
