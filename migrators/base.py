# Copyright (c) 2024 ntbies OSS. MIT License.

from typing import Any, Dict, List, Optional, Tuple
import logging

from rpc.jsonrpc import OdooClient
from utils.progress import ProgressTracker
from utils.fields import _get_stored_fields


SCALAR_TYPES = {"char", "text", "boolean", "integer", "float", "date", "datetime", "selection"}


class BaseMigrator:
    model: str = ""
    skip_update: bool = False

    def __init__(self, source: OdooClient, target: OdooClient, *, dry_run: bool = False, verbose: bool = False):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.source = source
        self.target = target
        self.dry_run = dry_run
        self.verbose = verbose
        # Track progress in local SQLite DB (resumable runs)
        self.progress = ProgressTracker(path="migration.db")
        self.resume: bool = True


        if not self.model:
            raise ValueError("model must be defined in subclass")

        self.src_fields = _get_stored_fields(self.source, self.model)
        self.tgt_fields = _get_stored_fields(self.target, self.model)
        self.intersection, self.m2o_fields = self._compute_field_intersection()

    def _record_mapping(self, model: str, src_id: Optional[int], dst_id: Optional[int]) -> None:
        if not src_id or not dst_id:
            return
        try:
            self.progress.set_mapping(model, int(src_id), int(dst_id))
            return
        except Exception:
            pass


    def _get_mapped_id(self, model: str, src_id: Optional[int]) -> Optional[int]:
        if not src_id:
            return None
        try:
            db_mapped = self.progress.get_mapped_id(model, int(src_id))
            if db_mapped:
                return int(db_mapped)
        except Exception:
            pass
        return None

    def _compute_field_intersection(self) -> Tuple[List[str], Dict[str, str]]:
        common = []
        m2o: Dict[str, str] = {}
        for name, s in self.src_fields.items():
            t = self.tgt_fields.get(name)
            if not t:
                continue
            if s.get("type") == t.get("type"):
                if s.get("type") in SCALAR_TYPES:
                    common.append(name)
                elif s.get("type") == "many2one" and s.get("relation") == t.get("relation"):
                    common.append(name)
                    m2o[name] = s.get("relation")
        blacklist = {"id", "write_uid", "write_date", "create_uid", "create_date", "__last_update", "display_name"}
        common = [f for f in common if f not in blacklist]
        return sorted(common), m2o

    def _resolve_m2o(self, field: str, relation: str, value: Any) -> Optional[int]:
        if value in (None, False):
            return None
        if isinstance(value, list) and value and isinstance(value[0], int):
            mapped = self._get_mapped_id(relation, value[0])
            if mapped:
                return mapped
            name = value[1] if len(value) > 1 else None
            if not name:
                return None
            matches = self.target.name_search(relation, name, limit=1)
            return matches[0][0] if matches else None
        if isinstance(value, int):
            mapped = self._get_mapped_id(relation, value)
            if mapped:
                return mapped
            rec = self.source.read(relation, [value], ["name"]) or []
            name = rec[0].get("name") if rec else None
            if name:
                matches = self.target.name_search(relation, name, limit=1)
                return matches[0][0] if matches else None
        if isinstance(value, str):
            matches = self.target.name_search(relation, value, limit=1)
            return matches[0][0] if matches else None
        return None

    def _remap_ids_for_create_for_model(self, model: str, vals: Dict[str, Any], tgt_fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        out: Dict[str, Any] = dict(vals)
        try:
            fields_meta = tgt_fields or _get_stored_fields(self.target, model)
        except Exception:
            fields_meta = {}
        for k, v in list(out.items()):
            if not k.endswith("_id") or v in (None, False):
                continue
            src_id: Optional[int] = None
            if isinstance(v, int):
                src_id = v
            elif isinstance(v, list) and v and isinstance(v[0], int):
                src_id = v[0]
            if not src_id:
                continue
            rel = None
            meta = fields_meta.get(k) if isinstance(fields_meta, dict) else None
            if isinstance(meta, dict):
                rel = meta.get("relation")
            if not rel:
                continue
            mapped = self._get_mapped_id(rel, src_id)
            if mapped and mapped != src_id:
                out[k] = int(mapped)
                if self.verbose:
                    self.logger.debug(f"Remapped {model}.{k}: {src_id} -> {mapped} using idmap")
        return out

    def _remap_ids_for_create(self, vals: Dict[str, Any]) -> Dict[str, Any]:
        return self._remap_ids_for_create_for_model(self.model, vals, self.tgt_fields)

    def _transform_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        vals: Dict[str, Any] = {}
        for f in self.intersection:
            v = record.get(f)
            if f in self.m2o_fields:
                rel = self.m2o_fields[f]
                ref_id = self._resolve_m2o(f, rel, v)
                vals[f] = ref_id and int(ref_id) or False
            else:
                vals[f] = v
        return vals

    def _filter_update_vals(self, vals: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in vals.items() if not k.endswith("uid") and not k.endswith("_id")}

    def _find_existing(self, vals: Dict[str, Any]) -> Optional[int]:
        # Basic de-duplication strategy: VAT, name or email+name
        domain: List[Any] = []
        vat = vals.get("vat")
        email = vals.get("email")
        name = vals.get("name")
        if vat:
            domain = [["vat", "=", vat]]
        elif email and name:
            domain = [["email", "=", email], ["name", "=", name]]
        elif name:
            domain = [["name", "=", name]]
        if not domain:
            return None
        ids = self.target.search(self.model, domain, limit=1)
        return ids[0] if ids else None

    def run(self, *, domain: Optional[List[Any]] = None, limit: Optional[int] = None, offset: int = 0, batch_size: int = 80):
        if self.verbose:
            self.logger.info(f"Model: {self.model}")
            self.logger.info(f"Fields to migrate ({len(self.intersection)}): {self.intersection}")
        remaining = limit
        current_offset = offset
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
                # Resume support: skip items already done
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
                    if existing_id:
                        if self.verbose:
                            self.logger.info(f"Update {self.model} {existing_id}: {vals.get('name')}")
                        if self.skip_update:
                            self.progress.mark_skipped(self.model, int(src_id), message="skip_update")
                            continue
                        upd_vals = self._filter_update_vals(vals)
                        if upd_vals:
                            self.target.write(self.model, [existing_id], upd_vals)
                        elif self.verbose:
                            self.logger.info(f"No updatable fields (excluding '*id') for {self.model} {existing_id}; skipping write.")
                        self._record_mapping(self.model, src_id, existing_id)
                        self.progress.mark_success(self.model, int(src_id), int(existing_id), updated=True)
                    else:
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
