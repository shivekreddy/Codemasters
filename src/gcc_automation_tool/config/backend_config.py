# basic imports
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

# tool-specific imports
from .type import *


@dataclass()
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