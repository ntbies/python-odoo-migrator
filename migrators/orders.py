# Copyright (c) 2024 ntbies OSS. MIT License.

from typing import Any, Dict, List, Optional, Tuple

from .base import BaseMigrator, SCALAR_TYPES
from utils.fields import _get_stored_fields


class SaleOrderMigrator(BaseMigrator):
    model = "sale.order"

    def _find_existing(self, vals: Dict[str, Any]) -> Optional[int]:
        name = vals.get("name") or vals.get("client_order_ref")
        if name:
            ids = self.target.search(self.model, [["name", "=", name]], limit=1)
            return ids[0] if ids else None
        partner_id = vals.get("partner_id")
        date_order = vals.get("date_order")
        domain: List[Any] = []
        if partner_id and date_order:
            domain = [["partner_id", "=", partner_id], ["date_order", "=", date_order]]
        elif partner_id:
            domain = [["partner_id", "=", partner_id]]
        if not domain:
            return None
        ids = self.target.search(self.model, domain, limit=1)
        return ids[0] if ids else None

    def _compute_line_intersection(self) -> Tuple[List[str], Dict[str, str]]:
        src = _get_stored_fields(self.source, "sale.order.line")
        tgt = _get_stored_fields(self.target, "sale.order.line")
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
        blacklist = {"id", "write_uid", "write_date", "create_uid", "create_date", "__last_update", "display_name", "order_id"}
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

    def run(self, *, domain: Optional[List[Any]] = None, limit: Optional[int] = None, offset: int = 0, batch_size: int = 500):
        if self.verbose:
            self.logger.info(f"Model: {self.model}")
            self.logger.info(f"Fields to migrate ({len(self.intersection)}): {self.intersection}")
        line_fields, line_m2o = self._compute_line_intersection()
        if self.verbose:
            self.logger.info(f"Order line fields to migrate ({len(line_fields)}): {line_fields}")
        remaining = limit
        current_offset = offset
        lines_field = "order_line"
        while True:
            take = batch_size if remaining is None else min(batch_size, remaining)
            if remaining == 0:
                break
            fields = ["id", *self.intersection]
            seen = set()
            fields = [f for f in fields if not (f in seen or seen.add(f))]
            records = self.source.search_read(self.model, domain or [], fields, limit=take, offset=current_offset)
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
                try:
                    existing_id = self._find_existing(vals)
                    if self.dry_run:
                        action = "update" if existing_id else "create"
                        self.logger.info(f"DRY-RUN {self.model}: {action} -> {vals.get('name')}")
                        self.progress.mark_skipped(self.model, int(src_id), message="dry-run")
                        continue
                    new_id: Optional[int] = None
                    if existing_id:
                        self._record_mapping(self.model, src_id, existing_id)
                        self.progress.mark_success(self.model, int(src_id), int(existing_id), updated=True)
                        if self.verbose:
                            self.logger.info(f"Skip existing {self.model} {existing_id}: {vals.get('name') or vals.get('ref')}")
                    else:
                        vals.pop("company_id", None)
                        src_lines = self.source.search_read(
                            "sale.order.line",
                            [["order_id", "=", int(src_id)]],
                            [*line_fields],
                            limit=0,
                        )
                        order_lines: List[Any] = []
                        for line_rec in src_lines:
                            line_vals = self._transform_line(line_rec, line_fields, line_m2o)
                            so_line = {
                                "order_id": new_id,
                                "product_id": line_vals.get("product_id"),
                                "product_uom_qty": line_vals.get("product_uom_qty"),
                                "price_unit": line_vals.get("price_unit"),
                                "discount": line_vals.get("discount"),
                                "name": line_vals.get("name"),
                                "currency_id": line_vals.get("currency_id"),
                                "qty_invoiced": line_vals.get("qty_invoiced"),
                                "qty_delivered": line_vals.get("qty_delivered"),
                            }
                            order_lines.append((0, 0, so_line))
                        if order_lines:
                            vals[lines_field] = order_lines
                        new_id = self.target.create(self.model, vals)
                        if self.verbose:
                            self.logger.info(f"Created {self.model} {new_id}: {vals.get('name')}")
                        self._record_mapping(self.model, src_id, new_id)
                        self.progress.mark_success(self.model, int(src_id), int(new_id), updated=False)
                except Exception as e:
                    self.progress.mark_failed(self.model, int(src_id), e)
                    if self.verbose:
                        self.logger.exception(f"Failed {self.model} {src_id}")
                    continue
            current_offset += len(records)
            if remaining is not None:
                remaining -= len(records)
