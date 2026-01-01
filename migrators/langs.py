# Copyright (c) 2024 ntbies OSS. MIT License.

from typing import Any, Dict, Optional, List

from .base import BaseMigrator


class LangMigrator(BaseMigrator):
    model = "res.lang"

    def _filter_update_vals(self, vals: Dict[str, Any]) -> Dict[str, Any]:
        return {"active": True}

    def _install_language(self, lang_code: str) -> bool:
        """
        Installs a language by invoking the base.language.install wizard.
        """
        result = self.target.search_read(self.model, [('code', '=', lang_code)], fields=['id', 'active'], limit=1,  context={'active_test': False})
        if not result or result[0].get('active'):
            self.logger.warning(f"Language {lang_code} already installed")
            return False
        lang_id = result[0].get('id')
        if self.verbose:
            self.logger.info(f"Installing language {lang_code} ({lang_id})")
        wizard_model = 'base.language.install'
        wizard_data = {
            'overwrite': False,
            'lang_ids': [(6, 0, [lang_id])],
        }

        ctx = {'active_ids': [lang_id],'active_id': lang_id, 'active_model': 'res.lang'}
        # For standard execute_kw
        wizard_id = self.target.create(wizard_model, wizard_data, context=ctx)
        return self.target.call_method(wizard_model, 'lang_install', [wizard_id])

    def run(self, *, domain: Optional[List[Any]] = None, limit: Optional[int] = None, offset: int = 0, batch_size: int = 80):
        if self.verbose:
            self.logger.info(f"Model: {self.model}")
            self.logger.info(f"Fields to migrate ({len(self.intersection)}): {self.intersection}")

        records = self.source.search_read(self.model, [('active', '=', True)], ['code', 'iso_code'], limit=200,)
        for rec in records:
            code = rec.get('code', rec.get('iso_code', 'en_US'))
            if self.dry_run:
                action = "skip install language"
                self.logger.info(f"DRY-RUN {self.model}: {action} -> {code}")
                continue
            self._install_language(code)

