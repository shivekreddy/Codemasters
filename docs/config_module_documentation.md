# config_loader API

Lightweight YAML configuration loading and validation framework with typed config objects.
Supports multiple config families via `config_type` dispatch.

---

## Supported config types

- `grid_code_requirements` → `RequirementsConfig`
- `project_info` → `ProjectConfig`

---

## Registry & Loader

### `_CONFIG_REGISTRY: Dict[str, Type[BaseConfig]]`
Internal registry that maps `config_type` to the corresponding `BaseConfig` subclass.

### `register_config(config_type: str)`
Decorator used to register a config class for a given `config_type`.

**Use case**: Extend the loader with new config types without modifying loader logic.

```python
@register_config("project_info")
class ProjectConfig(BaseConfig):
    ...
```

---

## Base Classes

### `BaseConfig` (dataclass)
Base class for all configuration objects.

**Fields**
- `config_type: str` – top‑level YAML identifier
- `raw: Dict[str, Any]` – original parsed YAML
- `extra: Dict[str, Any]` – unknown / unsupported top‑level keys

**Factory**
```python
BaseConfig.from_dict(d: dict) -> BaseConfig
```
Subclasses override this to parse their specific structure.

---

## Grid‑Code Requirements

### `Requirement` (dataclass)
Normalized representation of a single grid‑code requirement block.

**Fields**
- `name` – requirement identifier
- `applicable` – applicability flag
- `scope` – `common | type_1 | type_2`
- `evaluation_method` – `rms | loadflow | short-circuit | emt`
- `data` – remaining requirement data

---

### `RequirementsConfig(BaseConfig)`
Config class for `config_type: grid_code_requirements`.
Requires a `plant_type` to resolve scope ambiguity.

**Constructor**
```python
RequirementsConfig(raw: dict, plant_type: "type_1" | "type_2")
```

**Key Methods**

- `get_requirement(name, scope) -> Requirement`
- `iter_requirements() -> Iterator[Requirement]`
- `iter_applicable() -> Iterator[Requirement]`

**Attribute access**
```python
cfg.frequency_operation
```
Resolves in order:
1. `requirements_common`
2. active plant scope (`type_1` or `type_2`)

Raises if the requirement exists only in the non‑active scope.

---

## Project Configuration

### `BackendSpec` (dataclass)
Definition of one backend entry from `project_info`.

**Fields**
- `id` – unique backend identifier (e.g. `pf_rms`)
- `name` – `powerfactory | pscad | psse`
- `enabled` – whether backend is active
- `version` – optional software version
- `executable` – optional external executable path
- `simulation_setup_path` – path to simulation setup config YAML
- `element_mapper_path` – optional model mapping config
- `settings` – backend‑specific key/value settings

---

### `ProjectConfig(BaseConfig)`
Config class for `config_type: project_info`.

Holds **project‑specific metadata, configuration references, backend definitions,
result handling settings, logging settings, and project‑defined requirement bindings**.

**Parsed Sections**
- `meta`
- `plant`
- `configs`
- `backends` (parsed into `BackendSpec` objects)
- `results`
- `logging_cfg`
- `grid_requirements_project_data`

**Convenience Accessors**

```python
project.project_number
project.project_name
project.plant_type
project.requirements_path
project.grid_operator_overlays
project.enabled_backends
project.get_backend("pf_rms")
project.results_base_directory
project.results_run_id
project.log_level
project.log_file
```

No domain logic is implemented here – this class acts as a structured data container.

---

## ConfigLoader

### `ConfigLoader`
Generic loader and validator for all config types.

**Construction**
```python
ConfigLoader(path: str | Path, plant_type="type_2")
```

**Responsibilities**
1. YAML loading
2. Structural validation against template
3. Semantic validation (config‑type specific)
4. Dispatch to the correct `BaseConfig` subclass

---

### `load() -> BaseConfig`
Loads and validates a YAML file and returns a typed config object.

Raises:
- `FileNotFoundError`
- `ValueError`
- `yaml.YAMLError`

---

### Validation Pipeline

Validation is split into **two layers**:

#### 1) Structural Validation
Implemented in `check_structure()`:
- Missing keys → warning
- Type mismatches → warning
- Extra keys → info

#### 2) Semantic Validation
Dispatched by `config_type`:

- `validate_project_info()`
- `validate_grid_code_requirements()` (placeholder)

---

### `validate_project_info()` checks

Emits warnings for:
- Enabled backend without `simulation_setup_path`
- Grid‑operator overlay entry with null path
- Project‑defined requirement bindings that are empty or contain null fields

This enables safe manual editing and prepares for later GUI‑based generation.

---

## Extension Guidelines

To add a new config type:

1. Add a template YAML to `config_template`
2. Implement a `BaseConfig` subclass
3. Register it using `@register_config`
4. (Optional) add semantic validation hook

No changes to the loader core are required.

---

## Design Principles

- Structural vs semantic validation are kept separate
- No domain logic inside config objects
- Project‑specific choices are *bindings*, not requirement redefinitions
- Warnings over errors to support iterative setup

---

## Typical Usage

```python
project_cfg = ConfigLoader("./config/project_info.yaml").load()
requirements_cfg = ConfigLoader(project_cfg.requirements_path,
                                plant_type=project_cfg.plant_type).load()
```
