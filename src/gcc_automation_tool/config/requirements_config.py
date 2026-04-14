# basic imports
from typing import Dict, Any, Optional, Iterator
from dataclasses import dataclass

# tool-specific imports
from .type import *
from .base_config import register_config, BaseConfig

# logging
import logging
logger = logging.getLogger(__name__)


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
@dataclass()
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
