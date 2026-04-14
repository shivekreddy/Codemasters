from __future__ import annotations

from typing import Any, Dict, List

from src.gcc_automation_tool.config.config_loader import ConfigLoader
from src.gcc_automation_tool.config.project_config import ProjectConfig
from src.gcc_automation_tool.config.backend_config import BackendSpec
import logging

logger = logging.getLogger(__name__)

class Project:
    """
    Runtime representation of a GCC project.

    Wraps a validated ProjectConfig and provides:
    - orchestration logic
    - derived helpers
    - runtime state (results, status, caches)
    """

    def __init__(self, proj_config: ProjectConfig) -> None:
        self.config = proj_config
        logger.info(f'Loading Requirements from {self.requirements_path}.')
        self.requirements = ConfigLoader(self.requirements_path).load()
        # Runtime / execution state (not part of config)
        self.simulation_results: Dict[str, Any] = {}
        self.check_results: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Delegated project metadata
    # ------------------------------------------------------------------

    @property
    def project_name(self) -> str | None:
        return self.config.project_name

    @property
    def project_number(self) -> str | None:
        return self.config.project_number

    @property
    def plant_type(self):
        return self.config.plant_type

    # ------------------------------------------------------------------
    # Backend helpers
    # ------------------------------------------------------------------

    @property
    def enabled_backends(self) -> List[BackendSpec]:
        return self.config.enabled_backends

    def get_backend(self, backend_id: str) -> BackendSpec:
        return self.config.get_backend(backend_id)

    # ------------------------------------------------------------------
    # Paths and execution context
    # ------------------------------------------------------------------

    @property
    def requirements_path(self):
        return self.config.requirements_path

    @property
    def results_base_directory(self):
        return self.config.results_base_directory

    @property
    def results_run_id(self) -> str | None:
        return self.config.results_run_id

    # ------------------------------------------------------------------
    # Grid-code project data
    # ------------------------------------------------------------------

    @property
    def grid_requirements_project_data(self) -> Dict[str, Any]:
        return self.config.grid_requirements_project_data

    # ------------------------------------------------------------------
    # Generic access (escape hatch, optional)
    # ------------------------------------------------------------------

    def get_raw(self, path: str, default: Any = None) -> Any:
        """
        Dotted-path access into *raw* project_info dict.
        Intended only for special cases.
        """
        current: Any = self.config.raw
        for key in path.split("."):
            if not isinstance(current, dict):
                return default
            current = current.get(key, default)
        return current