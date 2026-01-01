# Copyright (c) 2024 ntbies OSS. MIT License.

from typing import Any, Dict, Optional, List

from .base import BaseMigrator


class TimesheetMigrator(BaseMigrator):
    model = "account.analytic.line"
    skip_update = True

    def _transform_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        vals = super()._transform_record(record)
        for k in ["message_last_post", "message_has_error", "message_has_sms_error", "company_id"]:
            if k in vals:
                vals.pop(k, None)
        return vals

    def _find_existing(self, vals: Dict[str, Any]) -> Optional[int]:
        employee_id = vals.get("employee_id")
        date = vals.get("date") or vals.get("date_time") or vals.get("create_date")
        project_id = vals.get("project_id")
        task_id = vals.get("task_id")
        domain = []
        if employee_id and date:
            domain = [["employee_id", "=", employee_id], ["date", "=", date]]
            if task_id:
                domain.append(["task_id", "=", task_id])
            elif project_id:
                domain.append(["project_id", "=", project_id])
        if not domain:
            return None
        ids = self.target.search(self.model, domain, limit=1)
        return ids[0] if ids else None

    def run(self, *, domain: Optional[List[Any]] = None, limit: Optional[int] = None, offset: int = 0,
            batch_size: int = 80):
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
                src_id = rec.get('id')
                if src_id is None:
                    continue
                if self.resume and self.progress.already_done(self.model, int(src_id)):
                    if self.verbose:
                        self.logger.info(f"Skip {self.model} {src_id}: already done")
                    continue
                self.progress.mark_started(self.model, int(src_id))
                vals = self._transform_record(rec)
                try:
                    if self.dry_run:
                        action = "skip create"
                        self.logger.info(f"DRY-RUN {self.model}: {action} -> {vals.get('name') or vals.get('ref')}")
                        self.progress.mark_skipped(self.model, int(src_id), message="dry-run")
                        continue

                    target_order_id = None
                    src_order_val = rec.get('order_id') or vals.get('order_id')
                    src_order_id = None
                    if isinstance(src_order_val, int):
                        src_order_id = src_order_val
                    elif isinstance(src_order_val, list) and src_order_val and isinstance(src_order_val[0], int):
                        src_order_id = src_order_val[0]
                    if src_order_id:
                        mapped = self._get_mapped_id('sale.order', int(src_order_id))
                        target_order_id = mapped if mapped else None

                    if not target_order_id:
                        self.progress.mark_skipped(self.model, int(src_id), message='order_not_mapped')
                        if self.verbose:
                            self.logger.warning(f"Skip {self.model} {src_id}: target sale.order mapping not found")
                        continue

                    sol_data = self.target.search_read(
                        'sale.order.line',
                        [('order_id', '=', target_order_id), ('product_id.type', '=', 'service')],
                        ['id', 'project_id', 'task_id']
                    )
                    if not sol_data:
                        self.progress.mark_skipped(self.model, int(src_id), message='sol_not_found')
                        continue
                    sol = sol_data[0]
                    target_sol_id = sol['id']
                    target_task_id = sol['task_id'][0] if sol.get('task_id') else False
                    target_project_id = sol['project_id'][0] if sol.get('project_id') else False
                    account_id = False
                    if target_project_id:
                        p_read = self.target.read('project.project', [target_project_id], ['account_id']) or []
                        if p_read:
                            project_data = p_read[0]
                            account_id = project_data['account_id'][0] if project_data and project_data.get('account_id') else False
                    if not account_id:
                        self.progress.mark_skipped(self.model, int(src_id), message='project_account_missing')
                        continue

                    timesheet_vals = {
                        'name': vals.get('name'),
                        'date': vals.get('date'),
                        'unit_amount': vals.get("unit_amount"),
                        'project_id': target_project_id,
                        'task_id': target_task_id,
                        'so_line': target_sol_id,  # ensures delivered qty updates on SO
                        'account_id': account_id,
                        'employee_id': False,
                    }
                    create_vals = self._remap_ids_for_create(timesheet_vals)
                    new_id = self.target.create('account.analytic.line', create_vals)
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
