"""12 Nov audit fixes with no database: D27 model tiers, the client-IP spoof guard (D53: CF-Connecting-IP),
and the metric names the SigNoz alert rules watch (D46)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request

from kx.agents.roles.registry import AgentRegistry, ModelTier
from kx.agents.website.router import _ip
from kx.core.contracts import AiRole

ROOT = Path(__file__).resolve().parents[2]

# D27: Sonnet (SMART) on turns that may discuss money; Haiku (FAST) on the rest.
FAST_ROLES = {
    AiRole.ORCHESTRATOR,
    AiRole.CONCIERGE,
    AiRole.PRODUCT,
    AiRole.MANUFACTURING,
    AiRole.LEAD_QUALIFICATION,
    AiRole.CONTACT_CAPTURE,
    AiRole.FOLLOW_UP,
}
SMART_ROLES = {
    AiRole.QUOTATION,
    AiRole.COMMERCIAL_APPROVAL,
    AiRole.COMMUNICATION,
    AiRole.DEAL_CLOSER,
    AiRole.REPEAT_SALES,
}


@pytest.mark.parametrize("role", sorted(FAST_ROLES | SMART_ROLES, key=lambda r: r.value))
def test_model_tier_matches_d27(role: AiRole) -> None:
    tier = AgentRegistry().get(role).tier
    assert tier == (ModelTier.FAST if role in FAST_ROLES else ModelTier.SMART)


def test_every_price_role_runs_on_the_smart_tier() -> None:
    """A role that may quote prices must never be on the cheap model (D27): money turns get Sonnet."""
    reg = AgentRegistry()
    for role in AiRole:
        spec = reg.get(role)
        if (
            spec.may_quote_prices and role is not AiRole.FOLLOW_UP
        ):  # follow-up only re-engages, never quotes live
            assert spec.tier == ModelTier.SMART, role


def _request(
    xff: str | None, client: str | None = "10.0.0.9", extra: dict[str, str] | None = None
) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    headers += [(k.encode(), v.encode()) for k, v in (extra or {}).items()]
    scope: dict[str, Any] = {
        "type": "http",
        "headers": headers,
        "client": (client, 12345) if client else None,
    }
    return Request(scope)


def test_x_forwarded_for_uses_the_last_entry_not_the_spoofable_first() -> None:
    # the ALB appends the real peer last; earlier entries are whatever the client sent
    assert _ip(_request("1.1.1.1, 2.2.2.2, 203.0.113.7")) == "203.0.113.7"
    assert _ip(_request("evil-spoof")) == "evil-spoof"  # one hop: the ALB's own append
    assert _ip(_request(None)) == "10.0.0.9"  # no header: the socket peer
    assert _ip(_request("", client=None)) == "unknown"


def test_trusted_client_ip_header_wins_only_when_configured() -> None:
    """D53: behind Cloudflare the visitor's address is CF-Connecting-IP; without the setting it is ignored."""
    req = _request("162.158.1.20", extra={"cf-connecting-ip": "198.51.100.4"})  # a Cloudflare edge hop
    assert _ip(req, "CF-Connecting-IP") == "198.51.100.4"
    assert _ip(req) == "162.158.1.20"
    v6 = _request("2606:4700:10::ac43:1", extra={"cf-connecting-ip": "2001:db8::7"})
    assert _ip(v6, "CF-Connecting-IP") == "2001:db8::7"


def test_cf_connecting_ip_from_a_non_cloudflare_peer_is_ignored() -> None:
    """Infra H7 (live test 18 Nov): a client that reaches the origin directly can send CF-Connecting-IP and
    pick any address, so the per-IP limits never trip. The header counts only from Cloudflare's ranges."""
    forged = _request("203.0.113.9", extra={"cf-connecting-ip": "198.51.100.4"})
    assert _ip(forged, "CF-Connecting-IP") == "203.0.113.9"
    no_proxy = _request(None, client="203.0.113.10", extra={"cf-connecting-ip": "1.2.3.4"})
    assert _ip(no_proxy, "CF-Connecting-IP") == "203.0.113.10"
    assert (
        _ip(_request("not-an-ip", extra={"cf-connecting-ip": "1.2.3.4"}), "CF-Connecting-IP") == "not-an-ip"
    )


def test_emitted_metric_names_are_the_ones_signoz_alerts_on() -> None:
    """D46: SigNoz alert rules watch these names (docs/PLATFORM_MODES.md, infra/server/README.md)."""
    for src in (
        "packages/kx-core-db/src/kx/core/fallback.py",
        "packages/kx-agent-framework/src/kx/agents/runtime/service.py",
    ):
        assert '"VendorSwitch"' in (ROOT / src).read_text(encoding="utf-8")
    assert '"SpendCap"' in (ROOT / "packages/kx-core-db/src/kx/core/spend.py").read_text(encoding="utf-8")
    readme = (ROOT / "infra/server/README.md").read_text(encoding="utf-8")
    assert "VendorSwitch" in readme and "QueueLagSeconds" in readme and "SpendCap" in readme
    # D6: a message the worker died on (stored lowercase by OpenTelemetry, as the README's rule names it)
    assert '"OutboundStuck"' in (ROOT / "packages/kx-core-automation/src/kx/automation/jobs.py").read_text(
        encoding="utf-8"
    )
    assert "outboundstuck" in readme
