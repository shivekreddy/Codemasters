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
from typing import Any, Type, Dict, Literal, Optional, Iterator, List

_CONFIG_REGISTRY: Dict[str, Type['BaseConfig']] = {}
PlantType = Literal["type_1", "type_2"]
ScopeName = Literal["common", "type_1", "type_2"]
EvalMethod = Literal["rms", "emt", "loadflow", "short-circuit"] 
BackendName = Literal["powerfactory", "pscad", "psse"]
logger = logging.getLogger(__name__)

# Add additional templates to this dictionary:
config_template = {
    'grid_code_requirements': "./config/master_templates/grid-code-requirements_template.yaml",
    'project_info': "./config/master_templates/project_info_template.yaml"
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
@dataclass(frozen=True)
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
#                                           Project Config
# -------------------------------------------------------------------------------------------------------------- 
@dataclass(frozen=True)
class BackendSpec:
    """Backend entry from project_info"""
    id: str
    name: BackendName
    enabled: bool
    version: Optional[str] = None
    executable: Optional[str] = None
    simulation_setup_path: Optional[str] = None
    element_mapper_path: Optional[str] = None
    settings: Dict[str, Any] = field(default_factory=dict)


@register_config('project_info')
@dataclass
class ProjectConfig(BaseConfig):
    """
    ProjectInfo config (config_type: project_info)
    Holds project-specific metadata, plant classification, config paths, backend definitions and project-specific bindings for grid code requirements.
    """
    
    meta: Dict[str, Any] = field(default_factory=dict)
    plant: Dict[str, Any] = field(default_factory=dict)
    configs: Dict[str, Any] = field(default_factory=dict)
    backends: List[BackendSpec] = field(default_factory=list)
    results: Dict[str, Any] = field(default_factory=dict)
    logging_cfg: Dict[str, Any] = field(default_factory=dict)
    grid_requirements_project_data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ProjectConfig":
        if d.get("config_type") != "project_info":
            logger.error("ProjectConfig expects config_type: project_info")
            raise ValueError("ProjectConfig expects config_type: project_info")
         
        # Differentiate between known and unknown top-level keys
        known_top = {
            "config_type",
            "meta",
            "plant",
            "configs",
            "backends",
            "results",
            "logging",
            "grid_requirements_project_data",
        }
        extra = {k: v for k, v in d.items() if k not in known_top}
        
        # Parse backends into BackendSpec objects (keep minimal validation here)
        backends_raw = d.get("backends") or []
        parsed_backends: List[BackendSpec] = []
        for i, b in enumerate(backends_raw):
            if not isinstance(b, dict):
                logger.error(f"backend[{i}] must be a mapping")
                raise TypeError(f"backend[{i}] must be a mapping")

            parsed_backends.append(
                BackendSpec(
                    id=str(b.get("id", "")).strip(),
                    name=b.get("name"),
                    enabled=bool(b.get("enabled", False)),
                    version=b.get("version"),
                    executable=b.get("executable"),
                    simulation_setup_path=b.get("simulation_setup_path"),
                    element_mapper_path=b.get("element_mapper_path"),
                    settings=b.get("settings") or {},
                )
            )

        return cls(
            config_type=d.get("config_type"),
            raw=d,
            extra=extra,
            meta=d.get("meta") or {},
            plant=d.get("plant") or {},
            configs=d.get("configs") or {},
            backends=parsed_backends,
            results=d.get("results") or {},
            logging_cfg=d.get("logging") or {},
            grid_requirements_project_data=d.get("grid_requirements_project_data") or {},
        )

    
    # ---------------------------------------------------------------------
    # Convenience accessors
    # ---------------------------------------------------------------------

    @property
    def project_number(self) -> Optional[str]:
        return self.meta.get("project_number")

    @property
    def project_name(self) -> Optional[str]:
        return self.meta.get("name")

    @property
    def plant_type(self) -> PlantType:
        # You may want to raise if missing/invalid; keeping minimal here:
        return self.plant.get("plant_type")

    @property
    def requirements_path(self) -> Optional[Path]:
        p = (self.configs.get("requirements") or {}).get("path")
        return Path(p) if p else None

    @property
    def grid_operator_overlays(self) -> List[Optional[Path]]:
        overlays = ((self.configs.get("overlays") or {}).get("grid_operator") or [])
        out: List[Optional[Path]] = []
        for item in overlays:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            out.append(Path(path) if path else None)
        return out

    @property
    def enabled_backends(self) -> List[BackendSpec]:
        return [b for b in self.backends if b.enabled]

    def get_backend(self, backend_id: str) -> BackendSpec:
        for b in self.backends:
            if b.id == backend_id:
                return b
        raise KeyError(f"Backend id not found: {backend_id}")

    @property
    def results_base_directory(self) -> Optional[Path]:
        p = self.results.get("base_directory")
        return Path(p) if p else None

    @property
    def results_run_id(self) -> Optional[str]:
        return self.results.get("run_id")

    @property
    def log_level(self) -> Optional[str]:
        return self.logging_cfg.get("level")

    @property
    def log_file(self) -> Optional[Path]:
        p = self.logging_cfg.get("file")
        return Path(p) if p else None


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
        match self.config_type:
            case 'project_info':
                self.validate_project_info(config, warnings)
            case 'grid_code_requirements':
                self.validate_grid_code_requirements(config, warnings)
            case _: pass
        


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
        
    def validate_grid_code_requirements(self, config: dict, warnings: list[str]):
        pass

    def validate_project_info(self, config: dict, warnings: list[str]):
        # Check if enabled backends are configured properly
        backends = config.get("backends", [])
        enabled_backends = [b for b in backends if b.get("enabled")]

        for backend in enabled_backends:
            bid = backend.get("id", "<unknown>")
            if not backend.get("simulation_setup_path"):
                warnings.append(
                    f"Backend '{bid}' is enabled but simulation_setup_path is null"
                )
                logger.warning("%s", warnings[-1])
        
        # Check if grid operator overlays are configured correctly
        overlays = config.get("configs", {}).get("overlays", {}).get("grid_operator", [])
        for ov in overlays:
            if ov.get("path") is None:
                warnings.append("Grid operator overlay specified but path is null")
                logger.warning("%s", warnings[-1])
       
        # Check if project specific requirements are present
        project_data = config.get("grid_requirements_project_data", {})
        for req_name, req_data in project_data.items():
            if req_data is None:
                warnings.append(
                    f"Project-specific data for '{req_name}' is defined but empty"
                )
                logger.warning("%s", warnings[-1])
                continue

            for key, val in req_data.items():
                if val is None:
                    warnings.append(
                        f"Project-specific field '{req_name}.{key}' is null"
                    )
                    logger.warning("%s", warnings[-1])



    
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


