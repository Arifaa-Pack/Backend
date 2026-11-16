"""Deploy-time settings for automation (env `KX_AUTO_*`, nested with `__`, e.g. KX_AUTO_WHATSAPP__APP_SECRET).

Business rules that the MD edits (approval window length, MD threshold ...) live in the CRM; what is
here are channel credentials and the platform-wide contact-hour rules from CONTRACTS.md §3.
"""

from __future__ import annotations

import uuid
from functools import cached_property, lru_cache

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from kx.core.timewindow import Window


class _Group(BaseModel):
    model_config = ConfigDict(frozen=True)


class WhatsAppSettings(_Group):
    graph_base_url: str = "https://graph.facebook.com"
    graph_version: str = "v23.0"
    phone_number_id: str = ""
    access_token: SecretStr = SecretStr("")
    app_secret: SecretStr = SecretStr("")
    verify_token: SecretStr = SecretStr("")
    template_language: str = "en"
    quote_delivery_minutes: int = 10  # D32: not delivered by then -> the same quote by email
    session_hours: int = 24  # Meta customer-service window: free-form messages only inside it


class SesSettings(_Group):
    """D44: Amazon SES is Stalwart's outbound relay (configured in Stalwart) and the app's backup sender
    (SES v2 API, `ses:SendEmail` only). `from_address` is the company sending address for both routes."""

    from_address: str = "sales@example.com"
    configuration_set: str | None = None
    region: str = "me-central-1"  # the SES region the domain identity was verified in


class StalwartSettings(_Group):
    """D44: the Stalwart mail server on our server, reached over JMAP (RFC 8620 / 8621). One account for the
    company sending address: quotes are sent from it, buyer replies and SES bounce / complaint reports land in
    its inbox and are read by the `automation.mail_poll` Beat job."""

    url: str = ""  # http://stalwart:8080 (internal); empty = Stalwart not configured (SES API only)
    username: str = ""  # the JMAP login of the sending account
    password: SecretStr = SecretStr("")
    timeout_seconds: float = 15.0
    poll_batch: int = 25

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.username)


class TelnyxSettings(_Group):
    """D43: Telnyx TeXML (TwiML-compatible) for staff alert calls and buyer call-backs (§14, §16)."""

    api_base_url: str = "https://api.telnyx.com"
    api_key: SecretStr = SecretStr("")  # empty = calls not configured (alerts stay WhatsApp only)
    account_sid: str = ""  # the TeXML account SID (Telnyx portal → TeXML applications)
    from_number: str = ""  # caller ID, E.164 (OPEN_QUESTIONS #70)
    public_key: SecretStr = SecretStr("")  # Ed25519 public key (base64) to verify webhook signatures
    ring_timeout_seconds: int = 30  # D31: not connected in 30 s -> next step on the ladder
    voice: str = "Polly.Joanna"
    language: str = "en-US"
    callback_ttl_seconds: int = 7200  # signed callback URLs expire after the call can no longer happen
    callback_secret: SecretStr = SecretStr("dev-only-change-me")  # signs the per-attempt callback tokens

    @property
    def enabled(self) -> bool:
        return bool(self.api_key.get_secret_value() and self.account_sid and self.from_number)


class AlertSettings(_Group):
    """Staff alerts, only 08:00-22:00 Dubai (§16). The reminder times (30 / 60 / 120 alert-hour minutes)
    come from the CRM setting `approval.reminder_minutes`, inside each QuoteSubmitted / ChangeRequested."""

    window_start: str = "08:00"
    window_end: str = "22:00"
    timezone: str = "Asia/Dubai"
    approval_request_template: str = "approval_request"  # 0 min: WhatsApp to the approver
    approval_reminder_template: str = "approval_reminder"  # 30 min: WhatsApp reminder
    change_request_template: str = "change_request"  # 0 min for a change request
    escalation_template: str = "approval_escalation"  # 120 min: WhatsApp to the manager (+ call)
    negotiation_paused_template: str = "negotiation_paused"
    notice_template: str = "staff_notice"  # one-line notices (manager notified, takeover, handover ...)
    final_quote_template: str = "final_quote_alert"  # §14.5: the salesperson takes over for payment
    # buyer-facing §17 templates (approved WhatsApp templates; outside the 24 h window only these may go)
    thank_you_template: str = "thank_you"  # WON
    order_update_template: str = "order_update"  # order status (client choice)
    # §6.5 / §8.2: the Requirement Summary, sent before the buyer ever wrote to WhatsApp, so it must be
    # an approved utility template with a document header; params: buyer name, the summary (OPEN #45)
    requirement_summary_template: str = "requirement_summary"
    # §8.1 the STOP confirmation is free-form text in the buyer's language inside the 24 h window
    # (kx.core.optout.CONFIRMATION), so it needs no template


class FollowUpSettings(_Group):
    """Buyer follow-ups: 09:00-18:00 buyer local time, at most `max_per_week` in any rolling 7 days."""

    window_start: str = "09:00"
    window_end: str = "18:00"
    max_per_week: int = Field(default=2, ge=1)
    default_timezone: str = "Asia/Dubai"
    templates: dict[str, str] = Field(
        default_factory=lambda: {
            "followup_d2": "followup_1",
            "followup_d5": "followup_2",
            "followup_d10": "followup_3",
            "reorder": "reorder_reminder",
            "custom": "followup_1",
        }
    )


class OptOutSettings(_Group):
    """§8.1 (D18). `ask_reason` mirrors the CRM setting `optout.ask_reason` (off until a lawyer confirms a
    "tell us why" line is not marketing); the container reads it from the CRM at start."""

    ask_reason: bool = False
    live_minutes: int = Field(default=10, ge=1)  # step 6: "replied in the last 10 minutes"
    unclear_answer_hours: int = Field(default=24, ge=1)  # step 6: no answer within 24 h = opt-out


class RateLimitSettings(_Group):
    """Token buckets: protect buyers from loops (per recipient) and providers from bursts (per channel)."""

    recipient_capacity: int = 6
    recipient_refill_per_minute: float = 2.0
    channel_capacity: int = 60
    channel_refill_per_minute: float = 600.0


class PriceSourceConfig(_Group):
    """One official page publishing a raw-material price. Results are *proposals*, never prices."""

    code: str = Field(pattern=r"^[a-z0-9_]+$")
    source_ref: uuid.UUID  # crm.price_sources.id
    url: HttpUrl
    material_code: str
    price_selector: str  # CSS selector for the element holding the number
    currency: str = Field(min_length=3, max_length=3)
    unit: str
    date_selector: str | None = None  # element holding the date the source states (ISO)
    terms_ok: bool = False  # scraping allowed by the site's terms (checked by a human)


class AutomationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KX_AUTO_", env_nested_delimiter="__", extra="ignore", frozen=True, env_ignore_empty=True
    )

    whatsapp: WhatsAppSettings = WhatsAppSettings()
    ses: SesSettings = SesSettings()
    stalwart: StalwartSettings = StalwartSettings()
    telnyx: TelnyxSettings = TelnyxSettings()
    alerts: AlertSettings = AlertSettings()
    followup: FollowUpSettings = FollowUpSettings()
    rate_limit: RateLimitSettings = RateLimitSettings()
    optout: OptOutSettings = OptOutSettings()
    price_sources: tuple[PriceSourceConfig, ...] = ()
    http_timeout_seconds: float = 15.0
    company_name: str = ""  # shown in buyer messages ("You're unsubscribed from <company> messages")
    public_base_url: str = "http://localhost:8000"  # https://api.<domain>: callbacks and unsubscribe links

    @cached_property
    def alert_window(self) -> Window:
        return Window.parse(self.alerts.window_start, self.alerts.window_end, self.alerts.timezone)

    def followup_window(self, buyer_timezone: str | None) -> Window:
        tz = buyer_timezone or self.followup.default_timezone
        return Window.parse(self.followup.window_start, self.followup.window_end, tz)


@lru_cache(maxsize=1)
def get_automation_settings() -> AutomationSettings:
    return AutomationSettings()
