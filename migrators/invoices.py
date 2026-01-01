# Copyright (c) 2024 ntbies OSS. MIT License.

from functools import lru_cache

from typing import Any, Dict, List, Optional, Tuple

from .base import BaseMigrator, SCALAR_TYPES
from utils.fields import _get_stored_fields


class InvoiceMigrator(BaseMigrator):
    model = "account.move"

    def _ensure_journal(self, vals: Dict[str, Any], src_rec: Dict[str, Any]) -> None:
        """Ensure vals has a valid target journal_id.

        Attempts resolution in this order:
        - Use existing vals["journal_id"] if already a valid int.
        - Use idmap/name resolution via _resolve_m2o from source journal value.
        - Try matching target journal by code+company, then name+company.
        When found, writes vals["journal_id"] and records id mapping for account.journal.
        """
        j_in_vals = vals.get("journal_id")
        if isinstance(j_in_vals, int) and j_in_vals > 0:
            return
        src_j = src_rec.get("journal_id") or j_in_vals
        dst_j_id: Optional[int] = None
        if src_j not in (None, False):
            dst_j_id = self._resolve_m2o("journal_id", "account.journal", src_j)
        src_j_id: Optional[int] = None
        if isinstance(src_j, int):
            src_j_id = src_j
        elif isinstance(src_j, list) and src_j and isinstance(src_j[0], int):
            src_j_id = src_j[0]
        if not dst_j_id and src_j_id:
            try:
                jrecs = self.source.read("account.journal", [src_j_id], ["code", "name", "company_id"]) or []
            except Exception:
                jrecs = []
            if jrecs:
                j = jrecs[0]
                code = j.get("code")
                name = j.get("name")
                comp_val = j.get("company_id")
                comp_src_id: Optional[int] = None
                if isinstance(comp_val, int):
                    comp_src_id = comp_val
                elif isinstance(comp_val, list) and comp_val and isinstance(comp_val[0], int):
                    comp_src_id = comp_val[0]
                comp_tgt_id = self._get_mapped_id("res.company", comp_src_id) if comp_src_id else None
                domain: List[Any] = []
                if code:
                    domain = [["code", "=", code]]
                    if comp_tgt_id:
                        domain.append(["company_id", "=", comp_tgt_id])
                    ids = self.target.search("account.journal", domain, limit=1)
                    if ids:
                        dst_j_id = ids[0]
                if not dst_j_id and name:
                    domain = [["name", "=", name]]
                    if comp_tgt_id:
                        domain.append(["company_id", "=", comp_tgt_id])
                    ids = self.target.search("account.journal", domain, limit=1)
                    if ids:
                        dst_j_id = ids[0]
        if dst_j_id:
            vals["journal_id"] = int(dst_j_id)
            if src_j_id:
                self._record_mapping("account.journal", src_j_id, dst_j_id)
                self.progress.mark_success(self.model, int(src_j_id), int(dst_j_id), updated=False)
            if self.verbose:
                self.logger.info(f"Resolved journal_id -> {dst_j_id}")
        else:
            if self.verbose:
                self.logger.warning("Warning: Could not resolve journal_id; record may fail to create.")

    def _compute_line_intersection(self) -> Tuple[List[str], Dict[str, str]]:
        src = _get_stored_fields(self.source, "account.move.line")
        tgt = _get_stored_fields(self.target, "account.move.line")
        common: List[str] = []
        m2o: Dict[str, str] = {}
        for name, s in src.items():
            t = tgt.get(name)
            if not t:
                continue
            if s.get("type") == t.get("type"):
                if s.get("type") in SCALAR_TYPES:
                    common.append(name)
                elif s.get("type") == "many2one" and s.get("relation") == t.get("relation"):
                    common.append(name)
                    m2o[name] = s.get("relation")
        blacklist = {
            "id",
            "write_uid",
            "write_date",
            "create_uid",
            "create_date",
            "__last_update",
            "display_name",
            "move_id",
        }
        common = [f for f in common if f not in blacklist]
        return sorted(common), m2o

    def _transform_line(self, rec: Dict[str, Any], line_fields: List[str], line_m2o: Dict[str, str]) -> Dict[str, Any]:
        vals: Dict[str, Any] = {}
        for f in line_fields:
            v = rec.get(f)
            if f in line_m2o:
                rel = line_m2o[f]
                ref_id = self._resolve_m2o(f, rel, v)
                vals[f] = ref_id and int(ref_id) or False
            else:
                vals[f] = v
        return vals

    @lru_cache(maxsize=1024)
    def _get_order_line_id(self, reference: str, product_id: int, name: str = None) -> Dict[str, Any]:
        base_domain = [
            ("order_id.name", "=", reference),
            ("product_id", "=", product_id)
        ]
        domain = base_domain + ([("name", "=", name)] if name else [])
        records = self.target.search_read('sale.order.line', domain, fields=['id'], limit=1)
        if not records and name:
            records = self.target.search_read('sale.order.line', base_domain, fields=['id'], limit=1)
        if records:
            return {'sale_line_ids': [(6, 0, [records[0]['id']])] }
        self.logger.warning(f"{base_domain} not found")
        return {}

    def run(self, *, domain: Optional[List[Any]] = None, limit: Optional[int] = None, offset: int = 0, batch_size: int = 50):
        if self.verbose:
            self.logger.info(f"Model: {self.model}")
            self.logger.info(f"Fields to migrate ({len(self.intersection)}): {self.intersection}")
        line_fields, line_m2o = self._compute_line_intersection()
        if self.verbose:
            self.logger.info(f"Invoice Line fields to migrate ({len(line_fields)}): {line_fields}")
        def _lines_field_name() -> str:
            try:
                meta = self.target.fields_get(self.model)
            except Exception:
                meta = {}
            if isinstance(meta, dict) and meta.get("invoice_line_ids", {}).get("type") == "one2many":
                return "invoice_line_ids"
            return "line_ids"

        lines_field = _lines_field_name()
        remaining = limit
        current_offset = offset
        while True:
            take = batch_size if remaining is None else min(batch_size, remaining)
            if remaining == 0:
                break
            fields = ["id", *self.intersection]
            seen = set()
            fields = [f for f in fields if not (f in seen or seen.add(f))]
            domain = [('move_type', 'in', ['out_invoice', 'out_refund', 'in_invoice', 'in_refund'])]
            records = self.source.search_read(self.model, domain, fields, limit=take, offset=current_offset)
            if not records:
                break
            for rec in records:
                src_id = rec.get("id")
                if src_id is None:
                    continue
                if self.resume and self.progress.already_done(self.model, src_id):
                    if self.verbose:
                        self.logger.info(f"Skip {self.model} {src_id}: already done")
                    continue
                self.progress.mark_started(self.model, int(src_id))
                vals = self._transform_record(rec)

                self._ensure_journal(vals, rec)
                try:
                    existing_id = self._find_existing(vals)
                    src_state_val = rec.get("state") or vals.get("state")
                    if self.dry_run:
                        action = "skip (exists)" if existing_id else "create"
                        self.logger.info(f"DRY-RUN {self.model}: {action} -> {vals.get('name') or vals.get('ref')}")
                        self.progress.mark_skipped(self.model, int(src_id), message="dry-run")
                        continue
                    if existing_id:
                        if self.verbose:
                            self.logger.info(f"Skip existing {self.model} {existing_id}: {vals.get('name') or vals.get('ref')}")
                        self._record_mapping(self.model, src_id, existing_id)
                        self.progress.mark_success(self.model, int(src_id), int(existing_id), updated=True)
                        continue

                    sale_order_ref = vals.get("invoice_origin")
                    create_vals = {
                        "currency_id": vals.get("currency_id"),
                        "partner_id": vals.get("partner_id"),
                        "invoice_date_due": vals.get("invoice_date_due"),
                        "name": vals.get("name"),
                        "invoice_payment_term_id": vals.get("invoice_payment_term_id"),
                        "payment_reference": vals.get("payment_reference"),
                        "journal_id": vals.get("journal_id"),
                        "move_type": "out_invoice",
                        "date": vals.get("date"),
                        "invoice_date": vals.get("invoice_date"),
                        "invoice_origin": sale_order_ref,
                    }

                    move_lines: List[Any] = []
                    if src_id:
                        src_lines = self.source.search_read(
                            "account.move.line",
                            [
                                ('move_id', '=', int(src_id)),
                                ('display_type', 'in', ['product', 'line_section', 'line_note'])
                            ],
                            [*line_fields],
                            limit=0,
                        )
                        for line_rec in src_lines:
                            line_vals = self._transform_line(line_rec, line_fields, line_m2o)
                            line_vals = self._remap_ids_for_create_for_model("account.move.line", line_vals)
                            mv_line = {
                                "product_id": line_vals.get("product_id"),
                                "name": line_vals.get("name"),
                                "quantity": line_vals.get("quantity"),
                                "price_unit": line_vals.get("price_unit"),
                                "discount": line_vals.get("discount"),
                                "currency_id": line_vals.get("currency_id"),
                            }
                            if sale_order_ref:
                                mv_line.update(self._get_order_line_id(sale_order_ref, line_vals.get("product_id"), line_vals.get("name")))
                            move_lines.append((0, 0, mv_line))
                    if move_lines:
                        create_vals[lines_field] = move_lines
                    new_id = self.target.create(self.model, create_vals)
                    if self.verbose:
                        self.logger.info(f"Created {self.model} {new_id} with {len(move_lines)} line(s): {create_vals.get('name') or create_vals.get('ref')}")
                    self._record_mapping(self.model, src_id, new_id)
                    self.progress.mark_success(self.model, int(src_id), int(new_id), updated=False)
                    if src_state_val == "posted":
                        try:
                            self.target.call_method(self.model, "action_post", [new_id])
                        except Exception:
                            raise
                except Exception as e:
                    self.progress.mark_failed(self.model, int(src_id), e)
                    if self.verbose:
                        self.logger.exception(f"Failed {self.model} {src_id}")
                    continue
            current_offset += len(records)
            if remaining is not None:
                remaining -= len(records)
