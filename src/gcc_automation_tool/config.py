"""
Configuration loader

Handles loading and validating YAML configuration files.
"""

from __future__ import annotations

import yaml
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Type, Dict, Literal, Optional, Iterator

_CONFIG_REGISTRY: Dict[str, Type['BaseConfig']] = {}
PlantType = Literal["type_1", "type_2"]
ScopeName = Literal["common", "type_1", "type_2"]
EvalMethod = Literal["rms", "emt", "loadflow", "short-circuit"] 
logger = logging.getLogger(__name__)

# Add additional templates to this dictionary:
config_template = {
    'grid_code_requirements': "./config/master_templates/grid-code-requirements_template.yaml"
}

def register_config(config_type: str):
    """Add config type to config registry - Use as @register_config on baseconfig subclass
    """
    def deco(cls):
        _CONFIG_REGISTRY[config_type] = cls
        return cls
    return deco


# --------------------------------------------------------------------------------------------------------------
#                                           Base-Config (Superclass)
# --------------------------------------------------------------------------------------------------------------


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


@register_config("grid_code_requirements")
@dataclass
class RequirementsConfig(BaseConfig):
    """Subclass of BaseConfig. 
    Requires plant type as a parameter (type_1 or type_2)
    """
    def __init__(self, raw: Dict[str, Any], plant_type: PlantType):
        self.raw = raw
        self.plant_type = plant_type
        self.meta = raw.get("meta", {})
        self.assumptions = raw.get("assumptions", {})
        self._sections: Dict[ScopeName, Dict[str, Any]] = {
            "common": raw.get("requirements_common") or {},
            "type_1": raw.get("requirements_type_1") or {},
            "type_2": raw.get("requirements_type_2") or {}
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any], plant_type: PlantType) -> "RequirementsConfig":
        if d.get("config_type") != "grid_code_requirements":
            # this is probably unnecessary, as the class is selected based on config_type
            logger.error("This class expects config_type: grid_code_requirements")
            raise ValueError("This class expects config_type: grid_code_requirements")
        return cls(d, plant_type=plant_type)

    def get_requirement(self, name: str, scope: ScopeName) -> Requirement:
        """Return requirement object for the chosen scope (common, type_1 or type_2)"""
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
        """Overrides standard attribute getter, returns the requirement object with the correct name.
        Convenience access:
          - first look in common
          - then look in bound plant_type scope (type_1 or type_2)
        """
        if name in self._sections["common"]:
            return self.get_requirement(name, "common")
        
        # if no scope is given, falls back to type_2
        active_scope: ScopeName = "type_1" if self.plant_type == "type_1" else "type_2"
        if name in self._sections[active_scope]:
            return self.get_requirement(name, active_scope)
        
        other_scope = "type_2" if active_scope == "type_1" else "type_1"
        if name in self._sections[other_scope]:
            logger.warning(f"Requirement '{name}' exists in '{other_scope}' but current plant_type='{self.plant_type}'.\nUse cfg.{other_scope}.{name} explicitly.")
            raise AttributeError(
                f"Requirement '{name}' exists in '{other_scope}' but current plant_type='{self.plant_type}'. "
                f"Use cfg.{other_scope}.{name} explicitly."
            )

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

# --------------------------------------------------------------------------------------------------------------
#                                           Config-Loader
# --------------------------------------------------------------------------------------------------------------  


class ConfigLoader:
    """Loads and validates configuration from YAML files."""
    
    def __init__(self, config_path: str | Path, plant_type: PlantType='type_2'):
        """
        Initialize the configuration loader.
        
        Args:
            config_path: Path to the YAML configuration file.
            plant_type: Type 1 or Type 2
            logger: Existing logger (optional), will create new one if necessary.
        """

        self.config_path = Path(config_path)
        self.plant_type = plant_type
        self._config: BaseConfig | None = None
        self.config_type = None

        logger.info(
            "Initializing ConfigLoader (path=%s, plant_type=%s)",
            self.config_path, self.plant_type
        )
    
    def load(self) -> BaseConfig:
        """
        Load the configuration from the YAML file.
        
        Returns:
            Configuration object with all type definitions and settings.
        
        Raises:
            FileNotFoundError: If the configuration file doesn't exist.
            yaml.YAMLError: If the YAML is malformed.
            ValueError: If required configuration fields are missing.
        """
        logger.info("%s", f"Loading Config File {self.config_path}")
        if not self.config_path.exists():
            logger.error(f"Configuration file not found: {self.config_path}")
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        
        if not data:
            logger.error("Configuration file is empty")
            raise ValueError("Configuration file is empty")
        
        if "config_type" not in data:
            logger.error("Configuration must contain 'config_type' section")
            raise ValueError("Configuration must contain 'config_type' section")
        
        self.config_type = data['config_type']

        # Check if a corresponding config template is available and linked
        if self.config_type not in config_template.keys():
            logger.error(f"Loaded config type is currently not supported. \nCurrently supported configs: \n{config_template.keys()}")
            raise ValueError("Loaded config type is currently not supported.")
        
        logger.info('YAML parsed successfully.')
        
        # Validate the config file
        warnings = self.validate(data)

        # create the correct config subclass with the validated dict
        cls = _CONFIG_REGISTRY.get(self.config_type)
        if not cls:
            logger.error(f"Unknown config_type '{self.config_type}'. Registered types: {list(_CONFIG_REGISTRY)}")
            raise ValueError(f"Unknown config_type '{self.config_type}'. Registered types: {list(_CONFIG_REGISTRY)}")
        
        sig = inspect.signature(cls.from_dict)
        if "plant_type" in sig.parameters:
            self._config = cls.from_dict(data, plant_type=self.plant_type)
        else:
            self._config = cls.from_dict(data)

        logger.info(f"Config File {self.config_path} (type: {self.config_type}) successfully loaded.")
        return self._config
    
    @property
    def config(self) -> BaseConfig:
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
        schema_path = config_template[self.config_type]
        if not schema_path: 
            logger.error("Path to config template could not be found.")
            raise ValueError("Path to config template could not be found.")

        with open(schema_path, "r", encoding="utf-8") as f:
            schema = yaml.safe_load(f)
        self.check_structure(schema, config, path="", warnings=warnings)

        if len(warnings)==0:
            logger.info("%s", 'Config structure successfully verified.')
    
        return warnings
    
    def check_structure(self, schema: Any, data: Any, path: str, warnings: list[str]):
        """Recursively check if structure of the given dictionary matches its corresponding config template. 
        Returns a list of mismatches/errors. 
        Does not check, if all fields in the tested dict are available inside the schema.
        """
        
        if isinstance(schema, dict):
            if not isinstance(data, dict):
                warnings.append(
                    f"Type mismatch at '{path}': expected dict, got {type(data).__name__}"
                )
                logger.warning("%s", f"Type mismatch at '{path}': expected dict, got {type(data).__name__}")
                return
            
            extra_keys = set(data.keys()) - set(schema.keys())
            for k in sorted(extra_keys):
                logger.info("%s", f"Extra key: {path}.{k}" if path else f"Extra key: {k}")
            
            for key, sub_schema in schema.items():
                current_path = f"{path}.{key}" if path else key

                if key not in data:
                    warnings.append(f"Missing key: {current_path}")
                    logger.warning("%s", f"Missing key: {current_path}")
                    continue

                self.check_structure(sub_schema, data[key], current_path, warnings)

            # Schema expects a list
        elif isinstance(schema, list):
            if not isinstance(data, list):
                warnings.append(
                    f"Type mismatch at '{path}': expected list, got {type(data).__name__}"
                )
                logger.warning("%s", f"Type mismatch at '{path}': expected list, got {type(data).__name__}")

        # Scalar → no structural validation needed
        else:
            pass
        
        

    
if __name__ == "__main__":
    from logger import setup_logging
    logger = setup_logging(level=logging.INFO, log_file="logs/run.log")
    req_4120_C:RequirementsConfig = ConfigLoader("./config/germany_vde-ar-n-4120_typeC.yaml", plant_type='type_2').load()
    print(f"Loaded Config is now of type {type(req_4120_C)}.")
    print(f"Type of iter_applicable is {type(req_4120_C.iter_applicable())}")
    for applicable_req in req_4120_C.iter_applicable():
        print(applicable_req.name)

    for req in req_4120_C.iter_requirements():
        print(f"Requirement {req.name}: Applicable = {req.applicable}")


