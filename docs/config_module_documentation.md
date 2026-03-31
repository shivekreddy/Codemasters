# config_loader API (YAML Config Loading & Validation)

## Overview
Loads YAML config files, validates their structure against a master template, and instantiates the correct config class based on `config_type`. Includes a concrete config implementation for `grid_code_requirements`.

---

## Globals / Types

### `_CONFIG_REGISTRY: Dict[str, Type[BaseConfig]]`
Registry mapping `config_type` → config class.

### `config_template: Dict[str, str]`
Maps `config_type` → path to schema/template YAML used for structure validation.

### Type aliases
- `PlantType = Literal["type_1", "type_2"]`
- `ScopeName = Literal["common", "type_1", "type_2"]`
- `EvalMethod = Literal["rms", "emt", "loadflow", "short-circuit"]`

---

## Functions

### `register_config(config_type: str) -> Callable`
**Use case:** Register a `BaseConfig` subclass for a `config_type`.

**Parameters**
- `config_type (str)`: Identifier used in YAML (`config_type: ...`)

**Returns**
- Decorator that registers the class in `_CONFIG_REGISTRY`.

**Example**
```python
@register_config("grid_code_requirements")
class RequirementsConfig(BaseConfig):
    ...
```

---

## Classes

## `BaseConfig` (dataclass)
**Use case:** Common superclass for all config types.

**Fields**
- `config_type: str` — must match YAML top-level `config_type`
- `raw: Dict[str, Any]` — original parsed YAML dict
- `extra: Dict[str, Any]` — reserved for unknown fields (optional use in subclasses)

### `BaseConfig.from_dict(d: Dict[str, Any]) -> BaseConfig`
**Use case:** Default construction from YAML dict (subclasses override).

**Parameters**
- `d`: parsed YAML as dict

**Returns**
- `BaseConfig` instance with `config_type` and `raw` assigned

---

## `Requirement` (dataclass)
**Use case:** Normalized container for a single requirement block.

**Fields**
- `name: str` — requirement key (e.g. `frequency_operation`)
- `applicable: Optional[bool]` — `true/false/null` from YAML
- `scope: ScopeName` — `"common" | "type_1" | "type_2"`
- `evaluation_method: Optional[EvalMethod]` — method tag for planning/verification
- `data: Dict[str, Any]` — requirement payload excluding `applicable` and `evaluation_method`

---

## `RequirementsConfig(BaseConfig)` (dataclass)
**Registered config_type:** `grid_code_requirements`

**Use case:** Access and iterate grid-code requirement blocks with plant-type scope binding.

### Constructor: `RequirementsConfig(raw: Dict[str, Any], plant_type: PlantType)`
**Parameters**
- `raw`: parsed YAML dict
- `plant_type`: `"type_1"` or `"type_2"`; binds scope resolution for ambiguous requirement names

**Attributes**
- `plant_type: PlantType`
- `meta: Dict[str, Any]`
- `assumptions: Dict[str, Any]`
- `_sections: Dict[ScopeName, Dict[str, Any]]` — stores `requirements_common/type_1/type_2`

### `RequirementsConfig.from_dict(d: Dict[str, Any], plant_type: PlantType) -> RequirementsConfig`
**Use case:** Factory for loader dispatch; validates `config_type`.

**Parameters**
- `d`: parsed YAML dict
- `plant_type`: `"type_1"` or `"type_2"`

**Raises**
- `ValueError` if `config_type != "grid_code_requirements"`

### `get_requirement(name: str, scope: ScopeName) -> Requirement`
**Use case:** Retrieve a specific requirement from a specific scope.

**Parameters**
- `name`: requirement key
- `scope`: `"common" | "type_1" | "type_2"`

**Returns**
- `Requirement`

**Raises**
- `KeyError` if not found in scope
- `TypeError` if block is not a mapping/dict

### `__getattr__(name: str) -> Requirement`
**Use case:** Convenience access without specifying scope.

**Resolution order**
1. Look in `common`
2. Look in bound plant scope (`type_1` or `type_2`)
3. If present only in the other scope → logs warning and raises `AttributeError`

**Examples**
```python
cfg.frequency_operation              # resolved from common
cfg.fault_ride_through               # resolved from active plant scope if present
```

### `iter_requirements() -> Iterator[Requirement]`
**Use case:** Planning/execution pass. Iterates:
- all `requirements_common`
- all requirements in the active plant scope (`requirements_type_1` or `requirements_type_2`)

### `iter_applicable() -> Iterator[Requirement]`
**Use case:** Planning/execution subset. Iterates only requirements where:
- `applicable is True`

---

## `ConfigLoader`
**Use case:** Load YAML, validate against template, instantiate correct config class.

### `ConfigLoader(config_path: str | Path, plant_type: PlantType = "type_2")`
**Parameters**
- `config_path`: path to YAML config file
- `plant_type`: `"type_1"` or `"type_2"` (passed to configs that accept it)

**Attributes**
- `config_path: Path`
- `plant_type: PlantType`
- `config_type: Optional[str]`
- `_config: Optional[BaseConfig]`

### `load() -> BaseConfig`
**Use case:** Main entry point. Loads and returns the instantiated config.

**Steps**
- Parse YAML
- Validate `config_type`
- Validate structure against template
- Instantiate correct config class using `_CONFIG_REGISTRY`
- Pass `plant_type` only if supported by `from_dict` signature (`inspect.signature`)

**Returns**
- Instance of a `BaseConfig` subclass (e.g. `RequirementsConfig`)

**Raises**
- `FileNotFoundError` — file missing
- `ValueError` — empty YAML, missing `config_type`, unsupported `config_type`, unknown registry type

### `config -> BaseConfig` (property)
**Use case:** Lazy accessor. Loads config on first access.

### `validate(config: dict) -> list[str]`
**Use case:** Validate config structure against the configured template for `self.config_type`.

**Returns**
- List of warnings (empty if no issues)

### `check_structure(schema: Any, data: Any, path: str, warnings: list[str]) -> None`
**Use case:** Recursive structural check used by `validate()`.

**Checks**
- Missing keys (schema key not present in config) → warning
- Type mismatches (dict/list expected) → warning
- Extra keys (present in config but not schema) → logged as INFO

---

## Typical Usage

```python
from logger import setup_logging
import logging

setup_logging(level=logging.INFO, log_file="logs/run.log")

cfg = ConfigLoader("./config/germany_vde-ar-n-4120_typeC.yaml", plant_type="type_2").load()

print(cfg.frequency_operation.applicable)

for req in cfg.iter_applicable():
    print(req.name, req.scope, req.evaluation_method)
```

---

## Extension Points

### Add new config types (e.g. `simulation_settings`)
1. Create a template YAML file and add it to `config_template`.
2. Create a subclass of `BaseConfig`.
3. Register it via `@register_config("simulation_settings")`.
4. Implement `from_dict()`.

```python
@register_config("simulation_settings")
@dataclass
class SimulationSettingsConfig(BaseConfig):
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SimulationSettingsConfig":
        return cls(config_type=d["config_type"], raw=d)
```
