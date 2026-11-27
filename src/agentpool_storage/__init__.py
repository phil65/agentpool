"""Storage provider package."""

__lazy_modules__ = [
    "agentpool_storage.base",
    "agentpool_storage.project_store",
]

from agentpool_storage.base import StorageProvider
from agentpool_storage.project_store import (
    ProjectStore,
    detect_project_root,
    discover_config_path,
    generate_project_id,
    resolve_config,
)

__all__ = [
    "ProjectStore",
    "StorageProvider",
    "detect_project_root",
    "discover_config_path",
    "generate_project_id",
    "resolve_config",
]
