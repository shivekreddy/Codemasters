"""
Configuration loader

Handles loading and validating YAML configuration files.
"""

from __future__ import annotations

import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Type, Dict, Literal, Optional, Iterator

_CONFIG_REGISTRY: Dict[str, Type['BaseConfig']]
PlantType = Literal["type_1", "type_2"]
ScopeName = Literal["common", "type_1", "type_2"]
EvalMethod = Literal["rms", "emt", "LoadFlow", "Short-Circuit"] 

def register_config(config_type: str):
    """Add config type to config registry - Use as @register_config on baseconfig subclass
    """
    def deco(cls):
        _CONFIG_REGISTRY[config_type] = cls
        return cls
    return deco

@dataclass
class TypeDefinition:
    """DEPRECATED
    Defines a type (LVRT, Harmonics, etc)."""
    
    name: str  # Type name
    config: dict[str, Any] = field(default_factory=dict)  # Type-specific configuration
    
    @classmethod
    def from_dict(cls, name: str, data: dict) -> TypeDefinition:
        """Create a TypeDefinition from a dictionary."""
        
        # CODE TO GET CONFIGURATION FROM DICTIONARY HERE
        
        
        return cls(
            name=name,
            config=data
        )


@dataclass
class BaseConfig:
    config_type: str
    raw: Dict[str, Any] = field(repr=False, default_factory=dict)   # Original dict
    extra: Dict[str, Any] = field(default_factory=dict)             # unknown fields

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BaseConfig":
        # default: store everything, subclasses will override
        return cls(config_type=d.get("config_type"), raw=d)
    


# --------------------------------------------------------------------------------------------------------------
#                                           Requirements-Config
# --------------------------------------------------------------------------------------------------------------


@dataclass
class Requirement:
    """Holds information for one set of requirements, including applicability, evaluation method and actual requirements to be fulfilled.
    """
    name: str
    applicable: Optional[bool]                                                      # True/False, None if not defined by grid code
    scope: ScopeName
    evaluation_method: Optional[EvalMethod]
    data: Dict[str, Any]                                                            # actual requirements



class ScopeView:
    def __init__(self, cfg: "RequirementsConfig", scope: ScopeName):
        self._cfg = cfg
        self._scope = scope

    def __getattr__(self, item: str) -> Requirement:
        return self._cfg.get_requirement(item, scope=self._scope)

    def get(self, item: str, default=None):
        try:
            return getattr(self, item)
        except KeyError:
            return default


@register_config("grid_code_requirements")
@dataclass
class RequirementsConfig(BaseConfig):
    """Subclass of BaseConfig. 

    """
    def __init__(self, raw: Dict[str, Any], plant_type: PlantType):
        self.raw = raw
        self.plant_type = plant_type
        self.meta = raw.get("meta", {})
        self.assumptions = raw.get("assumptions", {})
        self._sections = Dict[ScopeName, Dict[str, Any]] = {
            "common": raw.get("requirements_common", {}) or {},
            "type_1": raw.get("requirements_type_1", {}) or {},
            "type_2": raw.get("requirements_type_2", {}) or {}
        }


    @classmethod
    def from_dict(cls, d: Dict[str, Any], plant_type: PlantType) -> "RequirementsConfig":
        if d.get("config_type") != "grid_code_requirements":
            raise ValueError("This class expects config_type: grid_code_requirements")
        return cls(d, plant_type=plant_type)

    def get_requirement(self, name: str, scope: ScopeName) -> Requirement:
        block = self._sections[scope].get(name)
        if block is None:
            raise KeyError(f"Requirement '{name}' not found in scope '{scope}'")
        if not isinstance(block, dict):
            raise TypeError(f"Requirement '{name}' in '{scope}' must be a mapping")
        return Requirement(
            name=name,
            scope=scope,
            applicable=block.get("applicable"),
            evaluation_method=block.get("evaluation_method"),
            data={k: v for k, v in block.items() if k not in ("applicable", "evaluation_method")},
        )


    def __getattr__(self, name: str) -> Requirement:
        """
        Convenience access:
          - first look in common
          - then look in bound plant_type scope (type_1 or type_2)
        """
        if name in self._sections["common"]:
            return self.get_requirement(name, "common")

        active_scope: ScopeName = "type_1" if self.plant_type == "type_1" else "type_2"
        if name in self._sections[active_scope]:
            return self.get_requirement(name, active_scope)

        raise AttributeError(name)


    def iter_requirements(self) -> Iterator[Requirement]:
        """Iterate common + bound scope requirements (for planning/execution)."""
        yield from (self.get_requirement(n, "common") for n in self._sections["common"].keys())
        active_scope: ScopeName = "type_1" if self.plant_type == "type_1" else "type_2"
        yield from (self.get_requirement(n, active_scope) for n in self._sections[active_scope].keys())

    def iter_applicable(self) -> Iterator[Requirement]:
        """Iterate only applicable==True requirements."""
        for r in self.iter_requirements():
            if r.applicable is True:
                yield r

        


class ConfigLoader:
    """Loads and validates configuration from YAML files."""
    
    def __init__(self, config_path: str | Path):
        """
        Initialize the configuration loader.
        
        Args:
            config_path: Path to the YAML configuration file.
        """
        self.config_path = Path(config_path)
        self._config: Configuration | None = None
    
    def load(self) -> Configuration:
        """
        Load the configuration from the YAML file.
        
        Returns:
            Configuration object with all type definitions and settings.
        
        Raises:
            FileNotFoundError: If the configuration file doesn't exist.
            yaml.YAMLError: If the YAML is malformed.
            ValueError: If required configuration fields are missing.
        """
        print(f"Loading Config File {self.config_path}")
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        
        if not data:
            raise ValueError("Configuration file is empty")
        
        if "config_type" not in data:
            raise ValueError("Configuration must contain 'config_type' section")
        
        # Validate the config file
        warnings = self.validate(data)
        print(warnings)

        self._config = Configuration.from_dict(data)
        return self._config
    
    @property
    def config(self) -> Configuration:
        """Get the loaded configuration, loading it if necessary."""
        if self._config is None:
            self.load()
        return self._config
    
    def validate(self, config: dict) -> list[str]:
        """
        Validate the configuration structure against its template and return any warnings.
        Template is chosen automatically based on the 'config_type' field.
        
        Returns:
            List of warning messages (empty if no issues).
        """
        warnings = []

        #Select correct verification schema based on config file type
        match config['config_type']:
            case 'grid_code_requirements':
                schema_path = "./config/master_templates/grid-code-requirements_template.yaml"
                print(f'Verifying config against master template located in {schema_path}')
            case _:
                print(f"config_type is not set correctly in {self.config_path}. \n Cannot verify config file integrity.")
                exit()
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = yaml.safe_load(f)
        self.check_structure(schema, config, path="", warnings=warnings)

        if len(warnings)==0:
            warnings.append('Config structure verified.')

        # HERE WE CAN VALIDATE THE CONFIGURATION AND APPEND ANY WARNINGS TO THE LIST
        
        return warnings
    
    def check_structure(self, schema: Any, data: Any, path: str, warnings: list[str]):
        
        if isinstance(schema, dict):
            if not isinstance(data, dict):
                warnings.append(
                    f"Type mismatch at '{path}': expected dict, got {type(data).__name__}"
                )
                return


            
            for key, sub_schema in schema.items():
                current_path = f"{path}.{key}" if path else key

                if key not in data:
                    warnings.append(f"Missing key: {current_path}")
                    continue

                self.check_structure(sub_schema, data[key], current_path, warnings)

            # Schema expects a list
        elif isinstance(schema, list):
            if not isinstance(data, list):
                warnings.append(
                    f"Type mismatch at '{path}': expected list, got {type(data).__name__}"
                )

            # Scalar → no structural validation needed
            else:
                pass
        

    
if __name__ == "__main__":
    


    validate_config = ConfigLoader("./config/germany_vde-4110_typeC.yaml").load()
