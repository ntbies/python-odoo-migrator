# Copyright (c) 2024 ntbies OSS. MIT License.

"""
Top-level import surface for the Odoo Migrator library.

This module re-exports the most commonly used classes so users can write:

    from odoo_migrator import OdooClient, PartnerMigrator, BaseMigrator

while keeping backwards compatibility with the existing internal package
structure (migrators.*, rpc.*).
"""

from rpc.jsonrpc import OdooClient
from migrators.base import BaseMigrator
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
from main import MigrationRunner

__all__ = [
    "OdooClient",
    "BaseMigrator",
    "PartnerMigrator",
    "ProductMigrator",
    "ProjectMigrator",
    "SaleOrderMigrator",
    "InvoiceMigrator",
    "TimesheetMigrator",
    "PaymentMigrator",
    "LangMigrator",
    "MigrationRunner",
]
