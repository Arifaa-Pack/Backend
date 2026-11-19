"""Kairox automation: the channels (WhatsApp, email through Stalwart / SES, Telnyx calls), OutboundService
(the single way out), provider webhooks, opt-out, staff alerts, follow-ups and the background jobs on the
PostgreSQL schedule table, run by Celery in the app.

Public surface (ADR-0001, rule 3): the names an app (`arifaa_pack`) imports. Everything else is internal to
its feature slice (`kx/automation/<slice>/`, map in `docs/arch/AUTOMATION_AGENTS_SLICES.md`). Other
packages never import `kx.automation`: they use `kx.core.contracts` and events. The names are resolved
lazily (PEP 562), so importing one automation module does not load the whole package.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kx.automation import jobs, retention
    from kx.automation._shared.media import S3MediaStore
    from kx.automation._shared.settings import AutomationSettings, OptOutSettings
    from kx.automation.admin.router import build_router as admin_router
    from kx.automation.alerts.service import ApprovalLadder, CallBridge, PauseAlerts, VoiceAlerts
    from kx.automation.calls.channel import TelnyxCaller
    from kx.automation.cms.service import build_router as cms_router
    from kx.automation.email.chain import EmailChain, EmailVendor
    from kx.automation.email.channel import EmailSender
    from kx.automation.email.stalwart import JmapClient, MailboxPoller, StalwartSender
    from kx.automation.followups.service import FollowUps
    from kx.automation.handlers import EventHandlers
    from kx.automation.jobs import AutomationRuntime, JobScheduler
    from kx.automation.optout.router import UnsubscribeDeps, UnsubscribeLinks
    from kx.automation.optout.router import build_router as unsubscribe_router
    from kx.automation.outbound.killswitch import KillSwitch
    from kx.automation.outbound.ratelimit import Bucket, TokenBucketLimiter
    from kx.automation.outbound.service import OutboundService
    from kx.automation.scraping.service import PriceScraper
    from kx.automation.webhooks.router import WebhookDeps
    from kx.automation.webhooks.router import build_router as webhook_router
    from kx.automation.whatsapp.channel import WhatsAppClient

# public name -> (module, attribute); an empty attribute means the module itself
_EXPORTS: dict[str, tuple[str, str]] = {
    # wiring
    "jobs": ("kx.automation.jobs", ""),
    "retention": ("kx.automation.retention", ""),
    "AutomationRuntime": ("kx.automation.jobs", "AutomationRuntime"),
    "JobScheduler": ("kx.automation.jobs", "JobScheduler"),
    "EventHandlers": ("kx.automation.handlers", "EventHandlers"),
    "AutomationSettings": ("kx.automation._shared.settings", "AutomationSettings"),
    "OptOutSettings": ("kx.automation._shared.settings", "OptOutSettings"),
    "S3MediaStore": ("kx.automation._shared.media", "S3MediaStore"),
    # sending
    "OutboundService": ("kx.automation.outbound.service", "OutboundService"),
    "KillSwitch": ("kx.automation.outbound.killswitch", "KillSwitch"),
    "Bucket": ("kx.automation.outbound.ratelimit", "Bucket"),
    "TokenBucketLimiter": ("kx.automation.outbound.ratelimit", "TokenBucketLimiter"),
    "WhatsAppClient": ("kx.automation.whatsapp.channel", "WhatsAppClient"),
    "EmailSender": ("kx.automation.email.channel", "EmailSender"),
    "EmailChain": ("kx.automation.email.chain", "EmailChain"),
    "EmailVendor": ("kx.automation.email.chain", "EmailVendor"),
    "JmapClient": ("kx.automation.email.stalwart", "JmapClient"),
    "MailboxPoller": ("kx.automation.email.stalwart", "MailboxPoller"),
    "StalwartSender": ("kx.automation.email.stalwart", "StalwartSender"),
    "TelnyxCaller": ("kx.automation.calls.channel", "TelnyxCaller"),
    # staff alerts, follow-ups, price checks
    "ApprovalLadder": ("kx.automation.alerts.service", "ApprovalLadder"),
    "CallBridge": ("kx.automation.alerts.service", "CallBridge"),
    "PauseAlerts": ("kx.automation.alerts.service", "PauseAlerts"),
    "VoiceAlerts": ("kx.automation.alerts.service", "VoiceAlerts"),
    "FollowUps": ("kx.automation.followups.service", "FollowUps"),
    "PriceScraper": ("kx.automation.scraping.service", "PriceScraper"),
    # HTTP
    "webhook_router": ("kx.automation.webhooks.router", "build_router"),
    "WebhookDeps": ("kx.automation.webhooks.router", "WebhookDeps"),
    "unsubscribe_router": ("kx.automation.optout.router", "build_router"),
    "UnsubscribeDeps": ("kx.automation.optout.router", "UnsubscribeDeps"),
    "UnsubscribeLinks": ("kx.automation.optout.router", "UnsubscribeLinks"),
    "cms_router": ("kx.automation.cms.service", "build_router"),
    "admin_router": ("kx.automation.admin.router", "build_router"),
}

__all__ = [
    "ApprovalLadder",
    "AutomationRuntime",
    "AutomationSettings",
    "Bucket",
    "CallBridge",
    "EmailChain",
    "EmailSender",
    "EmailVendor",
    "EventHandlers",
    "FollowUps",
    "JmapClient",
    "JobScheduler",
    "KillSwitch",
    "MailboxPoller",
    "OptOutSettings",
    "OutboundService",
    "PauseAlerts",
    "PriceScraper",
    "S3MediaStore",
    "StalwartSender",
    "TelnyxCaller",
    "TokenBucketLimiter",
    "UnsubscribeDeps",
    "UnsubscribeLinks",
    "VoiceAlerts",
    "WebhookDeps",
    "WhatsAppClient",
    "admin_router",
    "cms_router",
    "jobs",
    "retention",
    "unsubscribe_router",
    "webhook_router",
]


def __getattr__(name: str) -> Any:
    try:
        module_name, attr = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value: Any = importlib.import_module(module_name)
    if attr:
        value = getattr(value, attr)
    globals()[name] = value
    return value
