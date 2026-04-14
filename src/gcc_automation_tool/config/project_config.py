# basic imports
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from pathlib import Path

# tool-specific imports
from .type import *
from .base_config import register_config, BaseConfig
from .backend_config import BackendSpec

# logging
import logging
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------------------------------------------
#                                           Project-Config
# --------------------------------------------------------------------------------------------------------------


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