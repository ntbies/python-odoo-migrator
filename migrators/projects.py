# Copyright (c) 2024 ntbies OSS. MIT License.

from typing import Any, Dict, Optional, List

from .base import BaseMigrator


class ProjectMigrator(BaseMigrator):
    model = "project.project"

    def _find_existing(self, vals: Dict[str, Any]) -> Optional[int]:
        name = vals.get("name")
        company_id = vals.get("company_id")
        domain = []
        if name and company_id:
            domain = [["name", "=", name], ["company_id", "=", company_id]]
        elif name:
            domain = [["name", "=", name]]
        if not domain:
            return None
        ids = self.target.search(self.model, domain, limit=1)
        return ids[0] if ids else None
