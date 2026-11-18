"""D5: the read-only reporting role (crm_0013) reads business data but no credentials, cost or minimum prices."""

from __future__ import annotations

import importlib.util
import pathlib
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import text

from kx.core.settings import LogicalDb

pytestmark = pytest.mark.db

# the test cluster's role (db/ddl/00_roles.sql); on the server it is <prefix>reporting_ro
ROLE = "reporting_ro"
MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "apps/arifaa-pack/src/arifaa_pack/migrations/crm/versions/0013_reporting_grants.py"
)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("crm_0013", MIGRATION)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _one(databases: Any, sql: str, **params: Any) -> Any:
    async with databases.transaction(LogicalDb.CRM) as s:
        return (await s.execute(text(sql), params)).scalar_one()


async def test_every_crm_table_is_classified_for_reporting(databases: Any) -> None:
    """A new crm table must be put in ALLOW, LIMITED or DENY on purpose (a later migration grants it)."""
    m = _migration()
    async with databases.transaction(LogicalDb.CRM) as s:
        tables = {
            r[0]
            for r in await s.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'crm' AND table_type = 'BASE TABLE'"
                )
            )
        }
    known = set(m.ALLOW) | set(m.LIMITED) | set(m.DENY)
    assert tables - known == set(), "classify these crm tables for the reporting role (crm_0013 lists)"
    assert not set(m.ALLOW) & set(m.DENY)


@pytest.mark.parametrize(
    ("table", "readable"),
    [
        ("deals", True),
        ("quote_versions", True),
        ("orders", True),
        ("staff_credentials", False),  # password hashes, encrypted TOTP seeds
        ("auth_sessions", False),  # refresh-token hashes
        ("quote_send_grants", False),
        ("product_costings", False),  # cost (MD only)
        ("quote_line_costs", False),
        ("margin_rules", False),
    ],
)
async def test_reporting_role_table_access(databases: Any, table: str, readable: bool) -> None:
    got = await _one(databases, "SELECT has_table_privilege(:r, :t, 'SELECT')", r=ROLE, t=f"crm.{table}")
    assert got is readable


@pytest.mark.parametrize(
    ("table", "column", "readable"),
    [
        ("product_price_tiers", "min_price", False),  # the minimum the AI may never go below
        ("product_price_tiers", "list_price", True),
        ("quote_line_tiers", "min_price", False),
        ("product_categories", "min_discount_pct", False),
        ("audit_events", "after", False),  # price-list edits record the old and new minimum here
        ("audit_events", "action", True),
    ],
)
async def test_reporting_role_column_access(databases: Any, table: str, column: str, readable: bool) -> None:
    got = await _one(
        databases, "SELECT has_column_privilege(:r, :t, :c, 'SELECT')", r=ROLE, t=f"crm.{table}", c=column
    )
    assert got is readable


async def test_a_new_table_is_not_readable_by_default(databases: Any) -> None:
    """The default grant for future tables is gone: a table nobody classified stays private."""
    async with databases.transaction(LogicalDb.CRM) as s:
        await s.execute(text("CREATE TABLE IF NOT EXISTS crm.zz_unclassified (id int)"))
    try:
        assert (
            await _one(databases, "SELECT has_table_privilege(:r, 'crm.zz_unclassified', 'SELECT')", r=ROLE)
            is False
        )
    finally:
        async with databases.transaction(LogicalDb.CRM) as s:
            await s.execute(text("DROP TABLE IF EXISTS crm.zz_unclassified"))
