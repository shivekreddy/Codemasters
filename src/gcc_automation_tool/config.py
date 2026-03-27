"""
Configuration loader

Handles loading and validating YAML configuration files.
"""

from __future__ import annotations

import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TypeDefinition:
    """Defines a type (LVRT, Harmonics, etc)."""
    
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
class Configuration:
    """Complete configuration for the converter."""
    
    types: dict[str, TypeDefinition] = field(default_factory=dict)

    
    @classmethod
    def from_dict(cls, data: dict) -> Configuration:
        """Create Configuration from a dictionary."""



        types = {}
        
        # for name, type_data in data.get("types", {}).items():
        #     type_def = TypeDefinition.from_dict(name, type_data)
      
        #     types[name] = type_def
        
        return cls(
            types=types
        )


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
        Validate the configuration and return any warnings.
        
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
    
    sample_config = {'types': {'LVRT': {'description': 'Low Voltage Ride Through'}, 'Harmonics': {'description': 'Harmonic Distortion'}}}
    
    # config = Configuration.from_dict(sample_config)
    # print(config)

    validate_config = ConfigLoader("./config/germany_vde-4110_typeC.yaml").load()
