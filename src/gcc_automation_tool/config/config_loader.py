# basic imports
from __future__ import annotations
import yaml
import inspect
from typing import Any
from pathlib import Path

# tool-specific imports
from .type import *
from .base_config import BaseConfig, _CONFIG_REGISTRY

#logging
import logging
logger = logging.getLogger(__name__)

# Add additional templates to this dictionary:
config_template = {
    'grid_code_requirements': "./config/master_templates/grid-code-requirements_template.yaml",
    'project_info': "./config/master_templates/project_info_template.yaml"
}


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
