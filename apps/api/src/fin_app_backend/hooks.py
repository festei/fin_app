"""Project hooks."""
from typing import Any, Dict, Iterable, Optional

from kedro.config import ConfigLoader
from kedro.framework.hooks import hook_impl
from kedro.io import DataCatalog
from kedro.versioning import Journal

import os

from kedro.config import TemplatedConfigLoader

class ProjectHooks:
    @hook_impl
    def register_config_loader(self, conf_paths: Iterable[str]) -> TemplatedConfigLoader:
        return TemplatedConfigLoader(
            conf_paths,
            globals_dict={
                "POSTGRES_USER": os.environ.get("POSTGRES_USER"),
                "POSTGRES_PASSWORD": os.environ.get("POSTGRES_PASSWORD"),
                "POSTGRES_HOST": os.environ.get("POSTGRES_HOST"),
                "POSTGRES_PORT": os.environ.get("POSTGRES_PORT"),
                "POSTGRES_DB": os.environ.get("POSTGRES_DB"),
                "FINANCE_API": os.environ.get("FINANCE_API"),
                "SCHEME_DB": os.environ.get("SCHEME_DB"),
            },
        )
    @hook_impl
    def register_catalog(
        self,
        catalog: Optional[Dict[str, Dict[str, Any]]],
        credentials: Dict[str, Dict[str, Any]],
        load_versions: Dict[str, str],
        save_version: str,
        journal: Journal,
    ) -> DataCatalog:
        return DataCatalog.from_config(
            catalog, credentials, load_versions, save_version, journal
        )
