# Copyright (c) 2024 ntbies OSS. MIT License.

import argparse
import json
import logging
from typing import Optional, List, Dict, Type, Any

from migrators import (
    PartnerMigrator,
    ProductMigrator,
    ProjectMigrator,
    SaleOrderMigrator,
    InvoiceMigrator,
    TimesheetMigrator,
    PaymentMigrator,
    LangMigrator,
)
from rpc.jsonrpc import OdooClient
from utils.fields import compare_model_fields, format_comparison_report_table, collect_models_spec
from utils.progress import ProgressTracker, format_progress_report_table


def parse_domain(domain_str: Optional[str]):
    if not domain_str:
        return []
    try:
        return json.loads(domain_str)
    except Exception as e:
        raise SystemExit(f"Invalid domain JSON: {e}")


def build_client(prefix: str, args: argparse.Namespace) -> OdooClient:
    url = getattr(args, f"{prefix}_url")
    db = getattr(args, f"{prefix}_db")
    user = getattr(args, f"{prefix}_user")
    password = getattr(args, f"{prefix}_password")
    if not all([url, db, user, password]):
        raise SystemExit(f"Missing {prefix} connection parameters. Provide --{prefix}-url, --{prefix}-db, --{prefix}-user, --{prefix}-password")
    client = OdooClient(url=url, db=db, username=user, password=password)
    client.authenticate()
    return client


def _collect_conn_params_from_config(prefix: str, config: Dict[str, Any]) -> Dict[str, Any]:
    cfg_block: Dict[str, Any] = (config or {}).get(prefix, {}) if config else {}
    url = cfg_block.get("url")
    db = cfg_block.get("db")
    user = cfg_block.get("user") or cfg_block.get("username")
    password = cfg_block.get("password")
    protocol = cfg_block.get("protocol")  # 'auto' | 'jsonrpc' | 'json2'
    api_key = cfg_block.get("api_key")
    base_missing = [k for k, v in {"url": url, "db": db}.items() if not v]
    if base_missing:
        raise SystemExit(
            f"Missing {prefix} connection parameters: {', '.join(base_missing)}. Provide a --config file with a '{prefix}' block or set them via CLI."
        )
    if not api_key:
        auth_missing = [k for k, v in {"user": user, "password": password}.items() if not v]
        if auth_missing:
            raise SystemExit(
                f"Missing {prefix} authentication parameters: {', '.join(auth_missing)}. Either provide user/password or configure an api_key for JSON-2."
            )
    if api_key and not user:
        user = ""
    if api_key and not password:
        password = ""
    params: Dict[str, Any] = {"url": url, "db": db, "user": user, "password": password}
    if protocol:
        params["protocol"] = protocol
    if api_key:
        params["api_key"] = api_key
    return params


def build_client_from_config(prefix: str, config: Dict[str, Any]) -> OdooClient:
    params = _collect_conn_params_from_config(prefix, config)
    client = OdooClient(
        url=params["url"],
        db=params["db"],
        username=params["user"],
        password=params["password"],
        api_key=params.get("api_key"),
        protocol=params.get("protocol", "auto"),
    )
    client.authenticate()
    return client


class MigrationRunner:
    """Runner that can execute a migration given a configuration dict or CLI args.

    The configuration format supports:
    - source: {url, db, user|username, password, protocol?, api_key?}
    - target: {url, db, user|username, password, protocol?, api_key?}
    - All CLI options as top-level keys: progress_db, resume, model, limit, offset,
      domain, dry_run, compare_only, dump_modelspec, modelspec_path, output_format,
      report, report_only, verbose, posted_only
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}

    def run(self):
        verbose = bool(self.config.get("verbose", False))
        log_level = logging.DEBUG if verbose else logging.INFO
        logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        logger = logging.getLogger(self.__class__.__name__)

        if self.config.get("report_only"):
            tracker = ProgressTracker(path=self.config.get("progress_db", "migration.db"))
            report = tracker.summary()
            if self.config.get("output_format", "table") == "json":
                logging.getLogger("report").info(json.dumps(report, indent=2))
            else:
                logging.getLogger("report").info(format_progress_report_table(report))
            return

        source = build_client_from_config("source", self.config)
        target = build_client_from_config("target", self.config)

        migrator_registry: Dict[str, Optional[Type[Any]]] = {
            "partners": PartnerMigrator,
            "payments": PaymentMigrator,
            "products": ProductMigrator,
            "projects": ProjectMigrator,
            "orders": SaleOrderMigrator,
            "invoices": InvoiceMigrator,
            "timesheets": TimesheetMigrator,
            "langs": LangMigrator,
        }

        dependency_order = [
            "langs",
            "partners",
            "products",
            "orders",
            "timesheets",
            "invoices",
            "payments",
        ]

        def selected_models(sel: str) -> List[str]:
            if sel == "all":
                return dependency_order
            return ['langs'] + [sel]

        models_to_process = selected_models(self.config.get("model", "partners"))

        model_map = {
            "partners": "res.partner",
            "langs": "res.lang",
            "products": "product.template",
            "projects": "project.project",
            "orders": "sale.order",
            "invoices": "account.move",
            "timesheets": "account.analytic.line",
            "payments": "account.payment",
        }

        if self.config.get("dump_modelspec"):
            odoo_models = [model_map[k] for k in models_to_process if k in model_map]
            spec = collect_models_spec(source, target, odoo_models)
            try:
                modelspec_path = self.config.get("modelspec_path", "modelspec.json")
                with open(modelspec_path, "w", encoding="utf-8") as f:
                    json.dump(spec, f, ensure_ascii=False, indent=2, sort_keys=True)
                logger.info(f"Model specification written to: {modelspec_path}")
                logger.info(f"Included models: {', '.join(odoo_models)}")
            except Exception as e:
                raise SystemExit(f"Failed to write modelspec to {modelspec_path}: {e}")
            return

        if self.config.get("compare_only"):
            for key in models_to_process:
                cls = migrator_registry.get(key)
                model = model_map[key]
                if cls is None:
                    logger.info(f"[compare-only] Skipping {key}: migrator not yet implemented. Showing field comparison anyway.")
                report = compare_model_fields(source, target, model)
                if self.config.get("output_format", "table") == "json":
                    logging.getLogger("compare").info(json.dumps(report, indent=2))
                else:
                    logging.getLogger("compare").info(format_comparison_report_table(report))
            return

        domain_cfg = self.config.get("domain", "")
        if isinstance(domain_cfg, str):
            domain = parse_domain(domain_cfg)
        else:
            domain = domain_cfg or []

        for key in models_to_process:
            cls = migrator_registry.get(key)
            logger.info("-" * 60)
            logger.info(f"Starting migration for: {key}")
            if cls is None:
                logger.info(f"Skipping {key}: migrator not yet implemented. (No changes performed)")
                continue
            migrator = cls(source, target, dry_run=bool(self.config.get("dry_run", False)), verbose=verbose)
            try:
                migrator.progress = ProgressTracker(path=self.config.get("progress_db", "migration.db"))
            except Exception:
                pass
            try:
                migrator.resume = bool(self.config.get("resume", True))
            except Exception:
                pass
            limit = int(self.config.get("limit", 0)) or None
            offset = int(self.config.get("offset", 0))
            migrator.run(domain=domain, limit=limit, offset=offset)
        logger.info("-" * 60)
        logger.info("Migration run finished.")
        if self.config.get("report"):
            try:
                filter_models = [model_map[k] for k in models_to_process if k in model_map]
            except Exception:
                filter_models = []
            tracker = ProgressTracker(path=self.config.get("progress_db", "migration.db"))
            report = tracker.summary(models=filter_models or None)
            if self.config.get("output_format", "table") == "json":
                logging.getLogger("report").info(json.dumps(report, indent=2))
            else:
                logging.getLogger("report").info(format_progress_report_table(report))


def main(argv: Optional[List[str]] = None):
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=str)
    pre_args, _ = pre_parser.parse_known_args(argv)

    base_config: Dict[str, Any] = {}
    if getattr(pre_args, "config", None):
        try:
            with open(pre_args.config, "r", encoding="utf-8") as f:
                base_config = json.load(f)
        except Exception as e:
            raise SystemExit(f"Failed to read config file {pre_args.config}: {e}")

    def build_parser(config_defaults: Dict[str, Any]) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Odoo Migrator a migration toolkit (multi-model scaffold)")
        parser.add_argument("--config", type=str, help="Path to JSON config file with 'source' and 'target' blocks; CLI flags override values from the file")
        parser.add_argument("--source-url", help="Source Odoo base URL, e.g. https://src.example.com")
        parser.add_argument("--source-db", help="Source Odoo database name")
        parser.add_argument("--source-user", help="Source Odoo username (email/login)")
        parser.add_argument("--source-password", help="Source Odoo password (or leave empty when using api_key in config)")
        parser.add_argument("--target-url", help="Target Odoo base URL, e.g. https://dst.example.com")
        parser.add_argument("--target-db", help="Target Odoo database name")
        parser.add_argument("--target-user", help="Target Odoo username (email/login)")
        parser.add_argument("--target-password", help="Target Odoo password (or leave empty when using api_key in config)")

        parser.add_argument("--progress-db", type=str, default=config_defaults.get("progress_db", "migration.db"), help="Path to the SQLite database used to track migration progress (default: migration.db)")
        resume_default = bool(config_defaults.get("resume", True))
        parser.add_argument("--resume", dest="resume", action="store_true", default=resume_default, help="Resume from previous progress (default)")
        parser.add_argument("--no-resume", dest="resume", action="store_false", help="Start fresh and ignore saved progress")
        parser.add_argument(
            "--model",
            choices=[
                "partners",
                "users",
                "companies",
                "products",
                "projects",
                "orders",
                "invoices",
                "timesheets",
                "payments",
                "all",
            ],
            default=base_config.get("model", "partners"),
            help="Which migrator to run (or 'all' to run the full sequence)",
        )
        parser.add_argument("--limit", type=int, default=int(config_defaults.get("limit", 0)), help="Maximum number of records to process (0 = no limit)")
        parser.add_argument("--offset", type=int, default=int(config_defaults.get("offset", 0)), help="Number of records to skip from the beginning")
        domain_default = config_defaults.get("domain", "")
        if isinstance(domain_default, (list, dict)):
            domain_default = json.dumps(domain_default)
        parser.add_argument("--domain", type=str, default=domain_default, help="Odoo domain filter in JSON (e.g. '[[\"active\",\"=\",true]]'); can also be a list in config.json")
        parser.add_argument("--dry-run", action="store_true", default=bool(config_defaults.get("dry_run", False)), help="Run without creating/updating records on the target")
        parser.add_argument("--compare-only", action="store_true", default=bool(config_defaults.get("compare_only", False)), help="Only compare source/target model fields and print a report")
        parser.add_argument("--dump-modelspec", action="store_true", default=bool(config_defaults.get("dump_modelspec", False)), help="Dump field specifications for selected models to a JSON file")
        parser.add_argument("--modelspec-path", type=str, default=config_defaults.get("modelspec_path", "modelspec.json"), help="Output path for --dump-modelspec (default: modelspec.json)")
        parser.add_argument("--output-format", choices=["table", "json"], default=config_defaults.get("output_format", "table"), help="Output format for reports: table or json")
        parser.add_argument("--report", action="store_true", default=bool(config_defaults.get("report", False)), help="Print a progress report after migration")
        parser.add_argument("--report-only", action="store_true", default=bool(config_defaults.get("report_only", False)), help="Only print a progress report and exit (no migration)")
        parser.add_argument("--verbose", action="store_true", default=bool(config_defaults.get("verbose", False)), help="Enable verbose logging (DEBUG level)")
        parser.add_argument("--posted-only", action="store_true", default=bool(config_defaults.get("posted_only", False)), help="For accounting-related migrators, process only posted documents")
        return parser

    parser = build_parser(base_config)
    args = parser.parse_args(argv)

    effective: Dict[str, Any] = dict(base_config) if base_config else {}
    effective.setdefault("source", {})
    effective.setdefault("target", {})

    # Apply connection overrides from CLI if provided
    if getattr(args, "source_url", None):
        effective["source"]["url"] = args.source_url
    if getattr(args, "source_db", None):
        effective["source"]["db"] = args.source_db
    if getattr(args, "source_user", None):
        effective["source"]["user"] = args.source_user
    if getattr(args, "source_password", None):
        effective["source"]["password"] = args.source_password
    if getattr(args, "target_url", None):
        effective["target"]["url"] = args.target_url
    if getattr(args, "target_db", None):
        effective["target"]["db"] = args.target_db
    if getattr(args, "target_user", None):
        effective["target"]["user"] = args.target_user
    if getattr(args, "target_password", None):
        effective["target"]["password"] = args.target_password
    for key in [
        "progress_db",
        "resume",
        "model",
        "limit",
        "offset",
        "domain",
        "dry_run",
        "compare_only",
        "dump_modelspec",
        "modelspec_path",
        "output_format",
        "report",
        "report_only",
        "verbose",
        "posted_only",
    ]:
        effective[key] = getattr(args, key)

    runner = MigrationRunner(config=effective)
    runner.run()


if __name__ == "__main__":
    main()
