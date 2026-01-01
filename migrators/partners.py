# Copyright (c) 2024 ntbies OSS. MIT License.

from typing import Any, Dict, Optional

from .base import BaseMigrator


class PartnerMigrator(BaseMigrator):
    model = "res.partner"

    def _transform_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        vals = super()._transform_record(record)
        for k in ["message_last_post", "message_has_error", "message_has_sms_error", "company_id"]:
            if k in vals:
                vals.pop(k, None)
        return vals

    def _filter_update_vals(self, vals: Dict[str, Any]) -> Dict[str, Any]:
        return {
            k: v for k, v in vals.items() if k in [
                "name", "vat", "street", "zip", "city", "country_id", "company_registry", "state_id",
                "email", "phone", "website", "comment", "color", "currency_id", "is_company",
                "phone_sanitized", "commercial_company_name", "parent_id",
                "property_payment_term_id", "property_account_position_id"
            ]
        }

    def _find_existing(self, vals: Dict[str, Any]) -> Optional[int]:
        vat = vals.get("vat")
        email = vals.get("email")
        phone = vals.get("phone")
        is_company = vals.get("is_company", False)
        name = vals.get("name")
        domain = []
        if is_company and vat:
            domain = [["vat", "=", vat]]
        elif name and phone:
            domain = [["name", "=", name], ["phone", "=", phone]]
        elif name:
            domain = [["name", "=", name]]
        elif email:
            domain = [["email", "=", email]]
        if not domain:
            return None
        ids = self.target.search(self.model, domain, limit=1)
        return ids[0] if ids else None


