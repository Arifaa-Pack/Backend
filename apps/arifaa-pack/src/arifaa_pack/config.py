"""Client app settings (env prefix KX_APP_) + company.yaml. Platform settings stay in each package."""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]  # types-PyYAML not in the lock yet
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from kx.agents import Effort

COMPANY_YAML = pathlib.Path(__file__).with_name("company.yaml")


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KX_APP_", extra="ignore", env_ignore_empty=True)

    api_port: int = 8000
    forwarded_allow_ips: str = "127.0.0.1"  # uvicorn proxy headers: only from the Coolify proxy (D53)
    cms_url: str = "http://localhost:3000"  # Payload CMS (KB source)
    cms_api_key: SecretStr | None = None
    # M2: CORS with credentials (the refresh cookie) only for the CRM screens; these are also the only
    # browser origins /v1/auth accepts. The public website goes in cors_public_origins: it may call
    # /v1/public/* (chat, voice, unsubscribe) without credentials and nothing else.
    cors_origins: tuple[str, ...] = ("http://localhost:3001",)
    cors_public_origins: tuple[str, ...] = ()
    # 18 Nov: automatic daily FX rates for quotes in other currencies (false = manual rates only)
    fx_rates_auto: bool = True
    fx_rates_url: str = "https://open.er-api.com/v6/latest/AED"

    # ---- background jobs (D25: Celery 5.7 + Beat, Redis broker, PostgreSQL schedule table)
    celery_broker_url: str | None = None  # default: KX_REDIS_URL
    worker_concurrency: int = (
        3  # bench R1: same reply times as 10, 1.7 GB less RAM, fewer DB connections (D48)
    )
    beat_sweep_seconds: float = 30.0  # Recommendation: Beat sweeps the schedule table every 30 s
    relay_seconds: float = 5.0  # outbox relays (chat replies must not wait a minute)
    celery_visibility_timeout_seconds: int = 7200  # above the longest task (OPEN_QUESTIONS #60)

    # ---- monitoring (D46: OpenTelemetry -> SigNoz; the AI audit trail -> Phoenix or Langfuse)
    otlp_endpoint: str | None = None  # http://signoz-otel-collector:4318 ; None = no export
    beat_max_lag_seconds: float = 300.0  # /healthz/beat is 503 above this queue lag
    ops_alert_emails: tuple[str, ...] = ()  # vendor-switch alerts (OpsNotifier, D41)
    ai_trace_endpoint: str | None = None  # http://phoenix:6006/v1/traces (or Langfuse's /api/public/otel/...)
    ai_trace_headers: dict[str, str] = {}  # Langfuse: {"Authorization": "Basic <base64 pk:sk>"}
    phoenix_project: str = "pack"

    # ---- AI (D27, D51: Claude API direct; Gemini standby via KX_PLATFORM__LLM_STANDBY)
    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    llm_effort_fast: Effort | None = None  # None = kx.agents.llm.service.DEFAULT_OPTIONS (OPEN_QUESTIONS #76)
    llm_effort_smart: Effort | None = None
    llm_effort_judge: Effort | None = None
    # M7 layer 3: a FAST-model second opinion on replies that talk about money or quantities but passed the
    # deterministic price guard; "yes" or an error fails closed (regenerate once, then the safe reply)
    price_second_opinion: bool = True
    # 19 Nov: a WhatsApp or email message's AI turn waits this long for the buyer's next quick message and
    # answers them together (website chat is not merged); 0 = one turn per message
    reply_merge_seconds: float = Field(default=8.0, ge=0, le=60)

    # ---- knowledge search (D47: BGE-M3 or Qwen3 on TEI, chosen by KX_PLATFORM__EMBEDDINGS; full-text
    # fallback)
    embedding_url: str | None = (
        None  # base URL of the OpenAI-compatible server (no /v1), e.g. http://embeddings
    )
    embedding_api_key: SecretStr | None = None
    embedding_query_timeout_seconds: float = (
        2.0  # Fallback plan: "Embedding call fails or > 2 s" -> full-text
    )
    embedding_index_timeout_seconds: float = (
        120.0  # CPU re-indexing took ≈ 26 s per request under load (bench)
    )

    # ---- website + voice (§6, D52, D53)
    turnstile_secret_key: SecretStr | None = None
    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: SecretStr | None = None
    speech_url: str | None = None  # free STT / TTS server (services/speech), e.g. http://speech:8100
    voice_max_concurrent_free: int = 3  # free-version calls at once (CPU speech-to-text, OPEN_QUESTIONS #72)
    voice_idle_processes: int = 2  # warm LiveKit job processes
    cms_webhook_secret: SecretStr | None = None  # HMAC Payload signs publish webhooks with (§7, D22)

    # ---- caps (D34)
    spend_ai_usd_per_day: Decimal = Decimal(25)  # defaults = Heavy column / 30 (OPEN_QUESTIONS #52)
    spend_voice_minutes_per_day: Decimal = Decimal(840)
    spend_wa_templates_per_day: Decimal = Decimal(870)
    # M6: the anonymous website may use at most this share of the daily AI cap; then website turns get the
    # safe reply + hand-over while WhatsApp and email keep working
    spend_public_ai_share: Decimal = Field(default=Decimal("0.30"), gt=0, le=1)
    public_turns_per_minute: int = Field(default=60, ge=1)  # website AI turns from all visitors together
    public_sessions_per_ip_per_day: int = Field(default=20, ge=1)  # new website sessions per client IP

    # ---- quote PDFs (D37: headless Chromium, WeasyPrint backup)
    pdf_renderer: Literal["chromium", "weasyprint"] = "chromium"  # D37 primary engine
    chromium_path: str = "/usr/bin/chromium"


@dataclass(frozen=True, slots=True)
class Company:
    code: str
    name: str
    letterhead: dict[str, str]
    languages: tuple[str, ...]


@lru_cache(maxsize=1)
def app_settings() -> AppSettings:
    return AppSettings()


@lru_cache(maxsize=1)
def company() -> Company:
    raw: dict[str, Any] = yaml.safe_load(COMPANY_YAML.read_text(encoding="utf-8"))
    return Company(
        code=raw["code"],
        name=raw["name"],
        letterhead=raw.get("letterhead", {}),
        languages=tuple(raw.get("languages", ("en",))),
    )
