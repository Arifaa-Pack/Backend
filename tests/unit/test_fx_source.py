"""18 Nov automatic FX rates: the public rates service gives units per 1 AED; the CRM stores AED per unit."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from arifaa_pack.adapters import OpenRatesFx
from kx.core.errors import Unavailable


async def latest(body: dict[str, object], status: int = 200) -> tuple[date, dict[str, Decimal]]:
    transport = httpx.MockTransport(lambda _: httpx.Response(status, json=body))
    async with httpx.AsyncClient(transport=transport) as http:
        return await OpenRatesFx(http).latest()


async def test_rates_are_turned_into_aed_per_unit() -> None:
    as_of, rates = await latest(
        {
            "result": "success",
            "base_code": "AED",
            "time_last_update_unix": 1794960000,
            "rates": {"AED": 1, "USD": 0.272294, "SAR": 1.021103, "INR": 23.5, "BAD": 0},
        }
    )
    assert as_of == date(2026, 11, 18)
    assert round(rates["USD"], 4) == Decimal("3.6725")  # the AED peg
    assert round(rates["SAR"], 4) == Decimal("0.9793") and "BAD" not in rates


@pytest.mark.parametrize(("body", "status"), [({"result": "error"}, 200), ({}, 503)])
async def test_a_failing_source_raises(body: dict[str, object], status: int) -> None:
    with pytest.raises((Unavailable, httpx.HTTPStatusError)):
        await latest(body, status)
