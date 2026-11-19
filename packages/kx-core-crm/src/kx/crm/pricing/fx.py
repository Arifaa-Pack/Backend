"""Automatic daily FX rates (client decision 18 Nov; D38 "daily rates, the manager can override").

A daily job asks the app's rate source (`FxSource`, e.g. a free public rates service) for AED per unit of each
currency Arifa quotes in and stores them in `crm.fx_rates` (source `auto:<name>`). Quotes keep using the
latest rate on or before their date, and the rate date is printed on the quote (§10).

Safety rules (money):
* a rate the manager typed for that day (source `manual`) is never overwritten;
* a rate that moved more than 10 % from the previous one is not stored and the manager is told (a bad feed
  must not reach a quote); currencies the source did not return are reported too;
* if the source is down the job fails and quotes keep the latest stored rate (with its date on the PDF).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Final, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core import events as ev
from kx.core.event_payloads import StaffNotice
from kx.crm._shared import staff
from kx.crm._shared.journal import Actor, Emit, record
from kx.crm.models import FxRate

# GCC, the main trading currencies, and the markets of the website's languages (South Asia, China)
DEFAULT_CURRENCIES: Final = (
    "USD",
    "EUR",
    "GBP",
    "SAR",
    "QAR",
    "OMR",
    "KWD",
    "BHD",
    "INR",
    "PKR",
    "LKR",
    "CNY",
)
MAX_DAILY_CHANGE: Final = Decimal("0.10")
_PLACES: Final = Decimal("0.00000001")  # fx_rates.rate_to_aed is numeric(18, 8)
_ACTOR: Final = Actor.system("fx_rates")


@runtime_checkable
class FxSource(Protocol):
    name: str

    async def latest(self) -> tuple[date, Mapping[str, Decimal]]:
        """(rate date, AED per 1 unit of each currency)."""
        ...


@dataclass(slots=True)
class FxRun:
    as_of: date
    saved: dict[str, Decimal] = field(default_factory=dict)
    kept_manual: list[str] = field(default_factory=list)
    refused: dict[str, tuple[Decimal, Decimal]] = field(default_factory=dict)  # currency -> (previous, new)
    missing: list[str] = field(default_factory=list)


def wanted(mapped: Iterable[str], stored: Iterable[str], base: str = "AED") -> list[str]:
    return sorted({*DEFAULT_CURRENCIES, *(c.upper() for c in mapped), *(c.upper() for c in stored)} - {base})


async def update_rates(session: AsyncSession, source: FxSource, currencies: Iterable[str]) -> FxRun:
    as_of, rates = await source.latest()
    run = FxRun(as_of)
    for cur in currencies:
        new = rates.get(cur)
        if new is None or new <= 0:
            run.missing.append(cur)
            continue
        new = new.quantize(_PLACES)
        row = await session.get(FxRate, (cur, as_of), with_for_update=True)
        if row is not None and row.source == "manual":
            run.kept_manual.append(cur)
            continue
        previous = (
            await session.execute(
                select(FxRate.rate_to_aed)
                .where(FxRate.currency == cur, FxRate.as_of < as_of)
                .order_by(FxRate.as_of.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if previous and abs(new / previous - 1) > MAX_DAILY_CHANGE:
            run.refused[cur] = (previous, new)
            continue
        if row is None:
            session.add(FxRate(currency=cur, as_of=as_of, rate_to_aed=new, source=f"auto:{source.name}"))
        else:
            row.rate_to_aed, row.source = new, f"auto:{source.name}"
        run.saved[cur] = new
    await session.flush()
    emit: tuple[Emit, ...] = ()
    if run.refused or run.missing:
        manager = staff.staff_contact(await staff.manager_user(session))
        parts = [f"{c} {p} -> {n}" for c, (p, n) in sorted(run.refused.items())]
        text = "FX rates not updated automatically"
        if parts:
            text += f" (moved more than 10 %: {', '.join(parts)})"
        if run.missing:
            text += f" (not available: {', '.join(run.missing)})"
        text += ". Quotes use the last rate; check and enter the rate in Settings > FX if needed."
        emit = (
            Emit(
                ev.STAFF_NOTICE,
                "fx_rate",
                as_of.isoformat(),
                StaffNotice(deal_ref=None, notify=(manager,), text=text, key=f"fx:{as_of.isoformat()}"),
            ),
        )
    await record(
        session,
        _ACTOR,
        "fx.auto_update",
        "fx_rate",
        as_of.isoformat(),
        after={
            "source": source.name,
            "saved": {c: str(v) for c, v in run.saved.items()},
            "kept_manual": run.kept_manual,
            "refused": {c: [str(p), str(n)] for c, (p, n) in run.refused.items()},
            "missing": run.missing,
        },
        emit=emit,
    )
    return run
