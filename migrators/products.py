# Copyright (c) 2024 ntbies OSS. MIT License.

from typing import Any, Dict, Optional

from .base import BaseMigrator


class ProductMigrator(BaseMigrator):
    model = "product.template"

    def _find_existing(self, vals: Dict[str, Any]) -> Optional[int]:
        default_code = vals.get("default_code")
        if default_code:
            ids = self.target.search(self.model, [["default_code", "=", default_code]], limit=1)
            if ids:
                return ids[0]
        name = vals.get("name")
        uom_id = vals.get("uom_id")
        domain = []
        if name and uom_id:
            domain = [["name", "=", name], ["uom_id", "=", uom_id]]
        elif name:
            domain = [["name", "=", name]]
        if not domain:
            return None
        ids = self.target.search(self.model, domain, limit=1)
        return ids[0] if ids else None
