# Copyright (c) 2024 ntbies OSS. MIT License.

from typing import Any, Dict, Optional, List

from .base import BaseMigrator


class PaymentMigrator(BaseMigrator):
    model = "account.payment.register"

    journal_map = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.journal_map = self._get_journal_map()

    def _get_journal_map(self):
        src_js = self.source.search_read('account.journal', [], ['code', 'name'])
        dest_js = self.target.search_read('account.journal', [], ['id', 'code'])

        dest_lookup = {j['code']: j['id'] for j in dest_js}
        journal_map = {}
        for sj in src_js:
            old_id = sj['id']
            new_id = dest_lookup.get(sj['code'])
            if new_id:
                journal_map[old_id] = new_id

        return journal_map

    def _create_missing_journal(self, src_journal_data):
        journal_vals = {
            'name': src_journal_data['name'],
            'code': src_journal_data['code'],
            'type': src_journal_data['type'],
            'currency_id': src_journal_data.get('currency_id'),  # Ensure ID is mapped!
        }
        if src_journal_data['type'] == 'bank':
            journal_vals['bank_acc_number'] = src_journal_data.get('bank_acc_number')

        try:
            new_journal_id = self.target.create('account.journal',  journal_vals)
            self.logger.info(f"Created missing journal: {src_journal_data['name']} ({new_journal_id})")
            src_id = src_journal_data.get('id')
            if src_id:
                self._record_mapping('account.journal', int(src_id), int(new_journal_id))
            return new_journal_id
        except Exception as e:
            self.logger.error(f"Failed to create journal {src_journal_data['name']}: {e}")
            return None

    def _get_or_create_journal(self, old_journal_id):
        if old_journal_id in self.journal_map:
            return self.journal_map[old_journal_id]
        src_data = self.source.read('account.journal', [old_journal_id],
                               ['name', 'code', 'type', 'currency_id'])[0]
        existing = self.target.search('account.journal', [('code', '=', src_data['code'])], limit=1)
        if existing:
            self.journal_map[old_journal_id] = existing[0]
            return existing[0]
        new_id = self._create_missing_journal(src_data)
        self.journal_map[old_journal_id] = new_id
        return new_id

    def _transform_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        vals = super()._transform_record(record)
        for k in ["message_last_post", "message_has_error", "message_has_sms_error", "company_id"]:
            if k in vals:
                vals.pop(k, None)
        return vals

    def run(self, *, domain: Optional[List[Any]] = None, limit: Optional[int] = None, offset: int = 0, batch_size: int = 80):
        if self.verbose:
            self.logger.info(f"Model: {self.model}")
            self.logger.info(f"Fields to migrate ({len(self.intersection)}): {self.intersection}")

        all_payments = self.source.search_read(
            'account.payment',
            [('state', '=', 'posted')],
            ['name', 'amount', 'date', 'reconciled_invoice_ids', 'journal_id', 'payment_type']
        )
        for payment in all_payments:
            progress_model = 'account.payment'
            src_id = payment.get('id') or payment.get('name')  # prefer id; name as weak fallback
            if not src_id:
                continue
            if self.resume and self.progress.already_done(progress_model, int(src_id)):
                if self.verbose:
                    self.logger.info(f"Skip {progress_model} {src_id}: already done")
                continue
            self.progress.mark_started(progress_model, int(src_id))
            rec_ids = payment.get('reconciled_invoice_ids', [])
            if not rec_ids:
                self.progress.mark_skipped(progress_model, int(src_id), message='no_reconciled_invoices')
                continue
            if self.dry_run:
                action = "skip create"
                self.logger.info(f"DRY-RUN {self.model}: {action} -> {payment.get('name')}")
                self.progress.mark_skipped(progress_model, int(src_id), message='dry-run')
                continue
            try:
                new_invoice_id = self._get_mapped_id("account.move", payment.get('reconciled_invoice_ids')[0])
                if not new_invoice_id:
                    self.progress.mark_skipped(progress_model, int(src_id), message='invoice_not_mapped')
                    if self.verbose:
                        self.logger.warning(f"Skip {progress_model} {src_id}: target invoice mapping not found")
                    continue
                ctx = {
                    'active_model': 'account.move',
                    'active_ids': [new_invoice_id],
                    'active_id': new_invoice_id
                }

                wizard_vals = self.target.call_method(
                    self.model, 'default_get',
                    ids=[],
                    fields = ['journal_id', 'amount', 'payment_date', 'payment_type'],
                    context=ctx
                )
                journal_val = payment.get('journal_id')
                journal_src_id = None
                if isinstance(journal_val, list) and journal_val and isinstance(journal_val[0], int):
                    journal_src_id = journal_val[0]
                elif isinstance(journal_val, int):
                    journal_src_id = journal_val
                wizard_vals.update({
                    'journal_id': self._get_or_create_journal(journal_src_id) if journal_src_id else None,
                    'amount': payment.get('amount'),
                    'payment_date': payment.get('date'),
                })
                wiz_id = self.target.create(self.model,  wizard_vals, context=ctx)
                self.target.call_method(self.model, 'action_create_payments', [wiz_id], context=ctx)
                self.progress.mark_success(progress_model, int(src_id), None, updated=False)
            except Exception as e:
                self.progress.mark_failed(progress_model, int(src_id), e)
                if self.verbose:
                    self.logger.exception(f"Failed to create payment for source {src_id}")
                continue
