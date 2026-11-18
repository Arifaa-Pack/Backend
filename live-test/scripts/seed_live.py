"""Seed the BENCH databases (kxbench project, port 25432) with what the fake traffic needs.

* staff, catalogue and the §23 price list: the repo's own test seed (`ensure_price_list` in
  packages/kx-core-crm/tests/conftest.py), loaded by file path, not copied or changed;
* a customer WhatsApp number, a customer email identity and a staff-alerts number (channel_accounts);
* every template name the code uses, marked approved (message_templates).

Run with the project venv:  .venv/Scripts/python.exe resource-usage-bench/scripts/seed.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
PORT = os.environ.get("BENCH_PG_PORT", "35432")
for db in ("crm", "automation", "agents"):
    os.environ[f"KX_{db.upper()}_DSN"] = f"postgresql+asyncpg://pack_{db}_app:dev@localhost:{PORT}/pack_{db}"
os.environ.setdefault("KX_REDIS_URL", "redis://localhost:36379/0")

TEMPLATES = [
    "approval_request", "approval_reminder", "change_request", "approval_escalation", "negotiation_paused",
    "staff_notice", "final_quote_alert", "thank_you", "order_update", "requirement_summary",
    "followup_1", "followup_2", "followup_3", "reorder_reminder", "quote_ready", "reopen_24h",
]


def load_crm_conftest() -> object:
    spec = importlib.util.spec_from_file_location("_bench_crm_conftest", REPO / "packages/kx-core-crm/tests/conftest.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def main() -> None:
    from sqlalchemy import text

    from kx.core.db import Databases
    from kx.core.settings import LogicalDb, get_settings

    dbs = Databases(get_settings())
    try:
        conftest = load_crm_conftest()
        sku = await conftest.ensure_price_list(dbs)  # type: ignore[attr-defined]
        print(f"catalogue + price list seeded ({sku})")
        async with dbs.transaction(LogicalDb.AUTOMATION) as s:
            await s.execute(text(
                """
                INSERT INTO channel_accounts (channel, provider, external_account_id, display_name, address, purpose,
                                              secret_ref, status, region, countries, is_default)
                VALUES ('whatsapp','meta_cloud','100000000000001','Arifa Pack (bench)','+971400000001','customer',
                        'bench','active','gcc',ARRAY['AE','SA','OM','QA','KW','BH'],true),
                       ('whatsapp','meta_cloud','100000000000002','Arifa alerts (bench)','+971400000002',
                        'internal_alerts','bench','active','gcc',ARRAY[]::text[],true),
                       ('email','ses','Hamthan@kairoxaitech.com','Arifa Pack sales (live test)','Hamthan@kairoxaitech.com','customer',
                        'bench','active',null,ARRAY[]::text[],true)
                ON CONFLICT (provider, external_account_id) DO NOTHING
                """
            ))
            for name in TEMPLATES:
                for channel in ("whatsapp", "email"):
                    await s.execute(
                        text(
                            """
                            INSERT INTO message_templates (channel, name, language, category, provider_status, subject,
                                                           body, variables)
                            SELECT :channel, :name, 'en', 'utility', 'approved', :subject,
                                   'Hello {{1}}, this is a live-test template ' || :name || '.', ARRAY['1']
                            WHERE NOT EXISTS (SELECT 1 FROM message_templates
                                              WHERE channel = :channel AND name = :name AND language = 'en')
                            """
                        ),
                        {"channel": channel, "name": name, "subject": "Arifa Pack" if channel == "email" else None},
                    )
        print("channel accounts + templates seeded")
    finally:
        await dbs.dispose()


if __name__ == "__main__":
    asyncio.run(main())
