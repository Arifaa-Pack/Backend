"""18 Nov automatic daily FX rates against the database: stored for quotes, a manager's rate is never overwritten,
a jump above 10 % is refused and the manager told."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from conftest import Seed
from sqlalchemy import delete, select

from kx.core import events as ev
from kx.core.settings import LogicalDb
from kx.crm import models as m
from kx.crm.services import fx
from kx.crm.services.pricing import fx_with_date

pytestmark = pytest.mark.db
DAY = date(2031, 3, 3)  # far from other tests' rates
D = Decimal


class FakeSource:
    name = "fake"

    def __init__(self, rates: Mapping[str, Decimal], on: date = DAY) -> None:
        self.rates, self.on = rates, on

    async def latest(self) -> tuple[date, Mapping[str, Decimal]]:
        return self.on, self.rates


async def test_daily_rates_keep_manual_overrides_and_refuse_big_jumps(databases: Any, seed: Seed) -> None:
    async with databases.transaction(LogicalDb.CRM) as s:
        await s.execute(delete(m.FxRate).where(m.FxRate.as_of >= DAY - timedelta(days=1)))
        s.add_all(
            [
                m.FxRate(
                    currency="EUR", as_of=DAY - timedelta(days=1), rate_to_aed=D("3.95"), source="auto:fake"
                ),
                m.FxRate(
                    currency="SAR", as_of=DAY, rate_to_aed=D("0.98"), source="manual"
                ),  # the manager's rate
            ]
        )
    async with databases.transaction(LogicalDb.CRM) as s:
        run = await fx.update_rates(
            s,
            FakeSource({"USD": D("3.6725"), "EUR": D("4.80"), "SAR": D("0.979")}),  # EUR +21 %: a bad feed
            ["USD", "EUR", "SAR", "QAR"],
        )
    assert run.saved == {"USD": D("3.67250000")} and run.kept_manual == ["SAR"]
    assert run.refused == {"EUR": (D("3.95000000"), D("4.80000000"))} and run.missing == ["QAR"]

    async with databases.transaction(LogicalDb.CRM) as s:
        usd = await fx_with_date(s, "USD", DAY)
        eur = await fx_with_date(s, "EUR", DAY)
        sar = await fx_with_date(s, "SAR", DAY)
        notices = list(
            (
                await s.execute(
                    select(m.OutboxEvent.payload).where(
                        m.OutboxEvent.event_type == ev.STAFF_NOTICE,
                        m.OutboxEvent.aggregate_id == DAY.isoformat(),
                    )
                )
            ).scalars()
        )
    assert usd == (D("3.67250000"), DAY)
    assert eur == (D("3.95000000"), DAY - timedelta(days=1))  # quotes keep yesterday's EUR, dated
    assert sar == (D("0.98000000"), DAY)  # the manager's rate stays
    assert len(notices) == 1 and "EUR" in notices[0]["text"] and "QAR" in notices[0]["text"]


def test_the_currencies_asked_for() -> None:
    assert "AED" not in fx.wanted(["AED", "jpy"], ["EUR"])
    assert {"USD", "SAR", "INR", "CNY", "JPY", "EUR"} <= set(fx.wanted(["jpy"], ["EUR"]))
