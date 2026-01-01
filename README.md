# Python Odoo Migrator
Python Odoo Migrator is a lightweight, scriptable toolkit to migrate data between two Odoo databases over HTTP using Odoo's JSON‑RPC (legacy) or JSON‑2 (modern, Odoo ≥ 19) APIs.

It provides:
- Resumable, idempotent migrations with a local SQLite progress database.
- A set of ready‑to‑use migrators (partners, products, orders, invoices, timesheets, payments, …).
- Field compatibility checks and model specification dumps to assist planning.
- A simple, extensible base class to implement your own migrators.

## Overview

Test compatibility have been performed to make sure the tool works to perform the following migrations:
- Between Odoo 13 through Odoo 19, including cross‑version migrations in both directions within this range.
- Community ↔ Enterprise migrations are supported and have been tested.

See the following section for more details:
- Check the section **Pre‑run checklist**: preparing the target Odoo environment can significantly affect invoices, taxes, and fiscal computations. Refer to the "Pre‑run checklist" section below.
- This package exposes python classes that can be embedded in your own scripts.his toolkit can also be embedded in your own Python scripts.

## Requirements
To run this tool, the only requirement is to have **Python 3.8+** installed on your system.

## Installation
To have the cli command  `odoo-migrator` available on your PATH, you can install the tool in one of two ways ways:

### Using pip
Install the latest release from PyPI:

```bash
pip install python-odoo-migrator
```

### From source
This way of installation requires cloning the repository and installing the package in editable mode. This allows you to modify the source code and run the latest version.

This mode of installation is the recommended one for developers.

```bash
git clone <your-fork-or-repo-url> odoo-migrator
cd odoo-migrator
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

After installation, the `odoo-migrator` command becomes available on your PATH.

> **Note**
> 
> This project intentionally uses only the Python standard library (no external requirements).


## Command‑line usage
The tool can be fully driven from the command line. You can pass connection parameters with flags or through a JSON configuration file. CLI flags override values from `--config`.

### With CLI flags
Here's an example of a simple migration from source to target. This will migrate the `partners` model from source to target, limiting the number of records to 100.

```bash
odoo-migrator \
  --source-url https://src.example.com \
  --source-db src_db \
  --source-user user@example.com \
  --source-password ****** \
  --target-url https://dst.example.com \
  --target-db dst_db \
  --target-user admin \
  --target-password ****** \
  --model partners \
  --limit 100 \
  --verbose
```

## Using a configuration file
You can run the command using a JSON config file (recommended) and overriding a few options from CLI:

```bash
odoo-migrator --config config.json --model invoices --posted-only --dry-run
```

Filtering source records with an Odoo domain (JSON string):

```bash
odoo-migrator --config config.json --model orders --domain '[ ("active","=",true) ]'
```

## Configuration
You can provide connection parameters via CLI flags or a JSON config file. CLI flags take precedence over the config file. The CLI parser now includes detailed `--help` messages for all options, and all CLI options can also be specified as top‑level keys in `config.json`.

### Example config file
You can use the following template to create a config json file.

```json
{
  "source": {
    "url": "https://your-source-odoo.example.com",
    "db": "your_source_db",
    "user": "source_user@example.com",
    "password": "<source_password>"
  },
  "target": {
    "url": "https://your-target-odoo.example.com",
    "db": "your_target_db",
    "user": "admin",
    "password": "<target_password>",
    "api_key": "<optional_api_key_for_json2>",
    "protocol": "auto"
  }
}
```

> **Notes**
> - `protocol` can be `auto` (default), `jsonrpc`, or `json2`.
> - If `api_key` is provided, `user`/`password` may be empty for JSON‑2 authentication (Odoo ≥ 20).

### Configuration parameters
Here is a table of all available configuration parameters that can be used in the configuration file.

| Key                               |      Type       |              Default              | Description                                                                                                                   |
|:----------------------------------|:---------------:|:---------------------------------:|:------------------------------------------------------------------------------------------------------------------------------|
| `source.url`                      |     string      |             required              | Source Odoo base URL (e.g., `https://src.example.com`).                                                                       |
| `source.db`                       |     string      |             required              | Source Odoo database name.                                                                                                    |
| `source.user` / `source.username` |     string      | required unless `source.api_key`  | Source username/login.                                                                                                        |
| `source.password`                 |     string      | required unless `source.api_key`  | Source password. Leave empty when using API key for JSON‑2.                                                                   |
| `source.protocol`                 |     string      |              `auto`               | Connection protocol: `auto`, `jsonrpc`, or `json2`.                                                                           |
| `source.api_key`                  |     string      |               none                | API key for JSON‑2 authentication (Odoo ≥ 20). If provided, `user`/`password` may be empty.                                   |
| `target.url`                      |     string      |             required              | Target Odoo base URL (e.g., `http://localhost:8069`).                                                                         |
| `target.db`                       |     string      |             required              | Target Odoo database name.                                                                                                    |
| `target.user` / `target.username` |     string      | required unless `target.api_key`  | Target username/login.                                                                                                        |
| `target.password`                 |     string      | required unless `target.api_key`  | Target password. Leave empty when using API key for JSON‑2.                                                                   |
| `target.protocol`                 |     string      |              `auto`               | Connection protocol: `auto`, `jsonrpc`, or `json2`.                                                                           |
| `target.api_key`                  |     string      |               none                | API key for JSON‑2 authentication (Odoo ≥ 20). If provided, `user`/`password` may be empty.                                   |
| `model`                           |     string      |            `partners`             | Which migrator to run: `partners`, `products`, `projects`, `orders`, `invoices`, `timesheets`, `payments`, `langs`, or `all`. |
| `limit`                           |       int       |                `0`                | Max number of records to process (0 = no limit).                                                                              |
| `offset`                          |       int       |                `0`                | Number of records to skip from the start.                                                                                     |
| `domain`                          | string or list  |               empty               | Filter for source records. As CLI: JSON string (e.g., `[["active","=",true]]`). In config: can be a parsed list.              |
| `dry_run`                         |      bool       |              `False`              | Do not write changes to the target. Useful for validation.                                                                    |
| `compare_only`                    |      bool       |              `False`              | Only compare fields and print a report; do not migrate.                                                                       |
| `dump_modelspec`                  |      bool       |              `False`              | Dump model field specifications for selected models.                                                                          |
| `modelspec_path`                  |     string      |         `modelspec.json`          | Output path for the modelspec JSON when `dump_modelspec` is enabled.                                                          |
| `output_format`                   |     string      |              `table`              | Report format: `table` or `json`. Applies to compare/report outputs.                                                          |
| `report`                          |      bool       |              `False`              | Print a progress report after a migration run.                                                                                |
| `report_only`                     |      bool       |              `False`              | Only print a progress report and exit; no migration.                                                                          |
| `progress_db`                     |     string      |          `migration.db`           | Path to the SQLite progress database.                                                                                         |
| `resume`                          |      bool       |              `True`               | Resume from previous progress (if available).                                                                                 |
| `verbose`                         |      bool       |              `False`              | Enable verbose logging (DEBUG).                                                                                               |
| `posted_only`                     |      bool       |              `False`              | For accounting‑related migrators, process only posted documents.                                                              |

## Pre‑run checklist
Before running migrations, ensure the target Odoo instance is correctly configured. These settings affect how records like invoices, taxes, and payments are created:

- Company identity
  - Legal name, address, VAT/tax ID
  - Email, phone, website
- Fiscal localization
  - Install the correct country localization module
  - Chart of accounts installed and set for the company
  - Taxes configured (sales/purchase), default tax mappings
  - Fiscal positions set up, including automatic detection rules if applicable
- Currency settings
  - Default company currency; currency rates and rounding precision configured
- Users and access rights
  - Ensure the migration/integration user has sufficient rights for all target models

> **Impact note**
> 
> Invoices, payments, and tax computations depend on fiscal localization and company configuration. 
> Set these up on the target before running any invoice/payment migrations to avoid inconsistent results.


## Troubleshooting
- Invalid domain JSON: ensure `--domain` is valid JSON (use double quotes and proper casing for true/false/null).
- Authentication errors: verify URL/DB/user/password. For Odoo ≥ 20 with JSON‑2, provide `api_key` or force `--config ... protocol: 'jsonrpc'` if you must use the legacy endpoint.
- SSL/HTTP issues: ensure your Odoo URL is reachable and not blocked by a proxy/SSL policy.
- Missing fields on target: consider adding custom fields/modules or adjust `_transform_record` to avoid unsupported fields.

## Writing a migrator (for developers)
Migrators are small classes that describe how to read records from the source, how to find/update/create them on the target, and how to transform field values between systems.

### Quick start
1) Create a new file in `migrators/` (e.g. `migrators/my_model.py`) and subclass `BaseMigrator`.

```python
from typing import Any, Dict, Optional
from .base import BaseMigrator


class MyModelMigrator(BaseMigrator):
    # Odoo model name this migrator handles
    model = "my.model"

    # Optional: define how to find an existing target record to UPDATE instead of CREATE
    def _find_existing(self, vals: Dict[str, Any]) -> Optional[int]:
        # Example: match by a unique external ID or a natural key like name+company
        ext = vals.get("x_external_code")
        if ext:
            ids = self.target.search(self.model, [["x_external_code", "=", ext]], limit=1)
            return ids[0] if ids else None
        return None

    # Optional: transform a source record before creation/update
    def _transform_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        # Adjust/rename fields, normalize values, or compute fields not present on source
        # You can reference Many2one fields and they will be remapped automatically
        # by the BaseMigrator when their relation model is already migrated.
        record = dict(record)
        # Example: map a custom boolean
        record["x_is_legacy"] = True
        return record
```

2) Register your migrator in `migrators/__init__.py`:

```python
from .my_model import MyModelMigrator

__all__ = [
    # ... keep existing entries ...
    "MyModelMigrator",
]
```

3) Expose it to the CLI by updating the registry in `main.py` (choices, registry, model map, and optionally dependency order):

```python
# In build_parser(...): add to --model choices
choices=[
    # ... existing ...
    "my_model",
]

# In migrator_registry
"my_model": MyModelMigrator,

# In model_map (Odoo technical model name)
"my_model": "my.model",

# In dependency_order if needed (ensure related models are migrated first)
# dependency_order = ["langs", "partners", ..., "my_model"]
```

4) Run your migrator:

```bash
odoo-migrator --config config.json --model my_model --verbose
```

### Implementation tips
- The base class automatically computes the intersection of scalar and compatible Many2one fields between source and target, and remaps Many2one IDs when the related models have already been migrated and mapped.
- Override `_find_existing` to implement idempotent updates (avoid duplicates).
- Override `_transform_record` to tweak values or drop unsupported fields.
- Use `--dry-run` to validate without writing to target.
- Use `--dump-modelspec` to inspect available fields on both ends.

## How to contribute
We welcome contributions! Here are some guidelines to follow:

1) Fork the repository and create a feature branch:
```bash
git checkout -b feat/my-migrator
```

2) Follow the existing code style and patterns. Avoid adding external dependencies without prior discussion.

3) If you add a new migrator:
- Include a short example in the README or a docstring.
- Register it in `migrators/__init__.py` and `main.py` (registry, dependency order, and CLI choices if needed).

4) Commit with clear messages and open a Pull Request describing the motivation, approach, and testing notes.

5) Please sanitize any sample config files—never commit real credentials.

If you have questions or proposals, open an issue to discuss.
