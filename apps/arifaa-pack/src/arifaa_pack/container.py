"""Composition root: builds every platform service once per process and wires the packages together.

Packages never import each other; this module is the only place kx.crm, kx.automation and kx.agents
meet. Event flow: each package relays its outbox -> `EventBus.publish` -> one durable job per subscribed
package in `automation.scheduled_jobs` -> a Celery worker runs it -> that package's handler (each handler
de-duplicates with its inbox). Celery itself lives in `arifaa_pack.jobqueue` / `arifaa_pack.celery_app`.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from typing import Any, Final

import aioboto3
import httpx
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import func, update

from arifaa_pack import texts_sync
from arifaa_pack.adapters import (
    EMBEDDING_MODELS,
    HttpEmbedder,
    OpenRatesFx,
    OpsNotifier,
    PayloadContentSource,
    PayloadProductWriter,
    PublicLimiter,
    RedisAuthThrottle,
    StaffCodeMailer,
    ThreadAddressBook,
    ThreadBurst,
    TurnstileVerifier,
    VoiceSlots,
)
from arifaa_pack.config import AppSettings, Company, app_settings, company
from arifaa_pack.jobqueue import CeleryKicker
from arifaa_pack.telemetry import heartbeat
from kx.agents import retention as agents_retention
from kx.agents.handlers import AgentHandlers
from kx.agents.knowledge import KnowledgeBase, KnowledgeIndexer
from kx.agents.memory import open_checkpointer
from kx.agents.models import (
    AnthropicApiProvider,
    GeminiApiProvider,
    ModelProvider,
    ModelRouter,
    tier_options,
)
from kx.agents.service import AgentService
from kx.agents.tracing import Tracer
from kx.agents.voice import VoiceRoute, VoiceRouter, voice_routes
from kx.agents.website import LiveKitConfig, WebsiteDeps
from kx.automation import jobs as auto_jobs
from kx.automation import retention as automation_retention
from kx.automation.alerts import ApprovalLadder, CallBridge, PauseAlerts, VoiceAlerts
from kx.automation.calls.channel import TelnyxCaller
from kx.automation.email.chain import EmailChain, EmailVendor
from kx.automation.email.channel import EmailSender
from kx.automation.email.stalwart import JmapClient, MailboxPoller, StalwartSender
from kx.automation.handlers import EventHandlers, FollowUps
from kx.automation.inbound.webhooks import WebhookDeps
from kx.automation.jobs import AutomationRuntime, JobScheduler
from kx.automation.media import S3MediaStore
from kx.automation.optout.unsubscribe import UnsubscribeDeps, UnsubscribeLinks
from kx.automation.outbound import OutboundService
from kx.automation.outbound.killswitch import KillSwitch
from kx.automation.outbound.ratelimit import Bucket, TokenBucketLimiter
from kx.automation.scraping.service import PriceScraper
from kx.automation.settings import AutomationSettings, OptOutSettings
from kx.automation.whatsapp.channel import WhatsAppClient
from kx.core import events as ev
from kx.core.auth import TokenIssuer
from kx.core.db import Databases
from kx.core.event_payloads import ProductContentChanged
from kx.core.events import Event, relay_once
from kx.core.fallback import FallbackChain
from kx.core.jobs import Job, JobRunner, JobSpec, JobStore, Kicker
from kx.core.logging import get_logger
from kx.core.passwords import SeedBox
from kx.core.platform import Field, Modes, order, resolved
from kx.core.retention import prune_batches
from kx.core.settings import AuthSettings, CoreSettings, Environment, LogicalDb, get_settings
from kx.core.spend import SpendCaps, SpendMeter
from kx.crm import (
    ApprovalStatusReaderImpl,
    ChromiumEngine,
    CrmDeps,
    CrmGatewayImpl,
    GrantConsumerImpl,
    Letterhead,
    PdfEngine,
    QuoteRenderer,
    S3PdfUploader,
    SettingsService,
    WeasyPrintEngine,
    retention_rules,
)
from kx.crm import jobs as crm_jobs
from kx.crm.models import Product  # ADR-0001 rule-3 baseline: cms_product_draft writes cms_product_id here

EventFn = Callable[[Event], Awaitable[Any]]
Periodic = Callable[[datetime], Awaitable[Any]]
CRM_EVENT: Final = "crm.handle_event"
log = get_logger(__name__)
AGENTS_EVENT: Final = "agents.handle_event"
CMS_PRODUCT_DRAFT: Final = "app.cms_product_draft"  # 18 Nov product workbook -> CMS draft
TRANSLATE_TEXT: Final = "app.translate_text"  # 18 Nov edited English message text -> other languages


class EventBus:
    """Fan-out over the schedule table: durable, retried, one job per subscribed package. Subscriptions are
    the union of each package's handler tables, so adding a handler in a package needs no change here."""

    def __init__(self, store: JobStore) -> None:
        self._store = store
        self._routes: dict[str, list[str]] = {}

    def route(self, event_type: str, task: str) -> None:
        self._routes.setdefault(event_type, []).append(task)

    def subscribers(self, event_type: str) -> tuple[str, ...]:
        """The tasks an event fans out to (read by `kx map`, ADR-0001 Phase 0)."""
        return tuple(self._routes.get(event_type, ()))

    async def publish(self, event: Event) -> None:
        payload = {"event": event.model_dump(mode="json")}
        for task in self._routes.get(event.event_type, ()):
            await self._store.enqueue(task, payload, dedup_key=f"event:{event.event_id}:{task}")


@dataclass(frozen=True, slots=True)
class Container:
    core: CoreSettings
    app: AppSettings
    automation_settings: AutomationSettings
    databases: Databases
    http: httpx.AsyncClient
    jobs: JobStore
    runner: JobRunner
    periodic: dict[str, Periodic]
    bus: EventBus
    automation_runtime: AutomationRuntime
    webhook_deps: WebhookDeps
    unsubscribe_deps: UnsubscribeDeps
    crm_deps: CrmDeps
    agent_service: AgentService
    website_deps: WebsiteDeps
    spend: SpendMeter
    redis: Any
    modes: Modes
    knowledge_indexer: KnowledgeIndexer
    voice_routes: dict[str, VoiceRoute]
    voice_switch: Callable[[str, str, str], None]  # LiveKit fallback adapters report switches here


def configured_vendors(
    core: CoreSettings, s: AppSettings | None = None, auto: AutomationSettings | None = None
) -> frozenset[str]:
    """D41: the vendors whose key or URL is set; `kx.core.platform.order` leaves the others out."""
    s = s or app_settings()
    auto = auto or AutomationSettings()
    have = {
        "claude": s.anthropic_api_key is not None,
        "gemini": s.gemini_api_key is not None,
        "deepgram": bool(os.environ.get("DEEPGRAM_API_KEY")),
        "elevenlabs": bool(os.environ.get("ELEVEN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")),
        "whisper": bool(s.speech_url),
        "piper": bool(s.speech_url),
        "telnyx": auto.telnyx.enabled,
        "meta": bool(auto.whatsapp.phone_number_id),
        "stalwart": auto.stalwart.enabled,
        "ses": True,  # credentials come from the AWS default chain (env); a failure is a normal send error
    }
    return frozenset(v for v, ok in have.items() if ok)


def _log_platform(modes: Modes, configured: Collection[str]) -> None:
    """Start-up: the resolved paid / free matrix once, and a warning per field that lost its paid vendor."""
    rows = resolved(modes, configured)
    log.info(
        "platform.resolved", plan=modes.plan, **{r.field.value: f"{r.mode}:{'>'.join(r.order)}" for r in rows}
    )
    for r in rows:
        if r.lost_paid:
            log.warning("platform.paid_vendor_missing", field=r.field.value, missing=",".join(r.missing))


def _model_router(s: AppSettings, modes: Modes, configured: Collection[str]) -> ModelRouter:
    """D27 / D51: Claude Sonnet 5.5 on money turns, Haiku 5.5 on the rest, directly through the Claude API.
    Gemini 3.8 Flash is the standby with KX_PLATFORM__LLM_STANDBY=gemini (client decision 18 Nov; run the
    eval suite against it first, `kx evals --vendor gemini`); when every model fails the agents give the
    safe reply and hand over."""
    providers: list[ModelProvider] = []
    for vendor in order(Field.LLM, modes, configured) or ("claude",):
        if vendor == "claude":
            providers.append(AnthropicApiProvider(s.anthropic_api_key))
        elif vendor == "gemini":  # 18 Nov: the client's standby when Claude is down
            if s.gemini_api_key is None:
                raise RuntimeError("KX_PLATFORM__LLM_STANDBY=gemini needs KX_APP_GEMINI_API_KEY (a paid key)")
            providers.append(GeminiApiProvider(s.gemini_api_key))
    options = tier_options(s.llm_effort_fast, s.llm_effort_smart, s.llm_effort_judge)
    return ModelRouter(providers, options)


def check_letterhead(co: Company, env: Environment, saved: Mapping[str, Any] | None = None) -> None:
    """D12(b): the letterhead prints on every quote PDF. Production refuses to start while a field is still a
    placeholder (REPLACE or empty) in company.yaml AND not set by staff in the CRM (`saved`, the company
    profile, 18 Nov); local and staging only warn."""
    fields = ("name", "address", "trn", "phone", "email")
    merged = {k: (saved or {}).get(k) or co.letterhead.get(k) for k in fields}
    missing = sorted(k for k in fields if "REPLACE" in (merged.get(k) or "REPLACE"))
    if not missing:
        return
    if env is Environment.PRODUCTION:
        raise RuntimeError(
            f"letterhead still has placeholders: {', '.join(missing)} "
            "(set them in the CRM company details or company.yaml)"
        )
    log.warning("company.letterhead_placeholders", fields=missing)


def _wire_events(
    runner: JobRunner,
    bus: EventBus,
    crm_rt: crm_jobs.CrmRuntime,
    agent_handlers: dict[str, EventFn],
    automation_types: frozenset[str],
    *,
    agent_last_resort: EventFn | None = None,
) -> None:
    async def crm_event(job: Job) -> None:
        await crm_jobs.dispatch(crm_rt, Event.model_validate(job.args["event"]))

    async def agents_event(job: Job) -> None:
        e = Event.model_validate(job.args["event"])
        try:
            await agent_handlers[e.event_type](e)
        except Exception:
            # D9: on the last attempt the buyer still gets the safe reply and the salesperson the deal
            if job.final_attempt and agent_last_resort is not None:
                try:
                    await agent_last_resort(e)
                except Exception:
                    log.exception("agents.last_resort_failed", event_id=str(e.event_id))
            raise

    runner.register(CRM_EVENT, JobSpec(crm_event))
    runner.register(AGENTS_EVENT, JobSpec(agents_event))
    for event_type in crm_jobs.HANDLERS:
        bus.route(event_type, CRM_EVENT)
    for event_type in agent_handlers:
        bus.route(event_type, AGENTS_EVENT)
    for event_type in automation_types:
        bus.route(event_type, auto_jobs.EVENT)


async def cms_product_draft(writer: PayloadProductWriter | None, dbs: Databases, job: Job) -> None:
    """Save one product's workbook details in the CMS as a draft and remember its CMS id in the CRM.
    Retried by the job store on any CMS error; without a CMS key (local dev) it is skipped with a warning."""
    content = ProductContentChanged.model_validate(Event.model_validate(job.args["event"]).payload)
    if writer is None:
        log.warning("cms.product_draft_skipped", code=content.code, reason="KX_APP_CMS_API_KEY is not set")
        return
    doc_id, created = await writer.save_draft(content)
    async with dbs.transaction(LogicalDb.CRM) as session:
        await session.execute(
            update(Product)
            .where(func.upper(Product.sku) == content.code.upper(), Product.cms_product_id.is_(None))
            .values(cms_product_id=doc_id)
        )
    log.info("cms.product_draft_saved", code=content.code, cms_id=doc_id, created=created)


@dataclass(frozen=True, slots=True)
class _PeriodicDeps:
    store: JobStore
    databases: Databases
    bus: EventBus
    automation: AutomationRuntime
    crm: crm_jobs.CrmRuntime
    http: httpx.AsyncClient
    settings: AppSettings
    company_code: str
    redis: Any
    core: CoreSettings
    archive: S3MediaStore | None


def _periodic(d: _PeriodicDeps) -> dict[str, Periodic]:
    """Beat entries by name (`arifaa_pack.schedule`): the app's own plus each package's job bodies."""

    async def dispatch_due(_: datetime) -> int:
        return await d.store.dispatch_due()

    async def relay_agents(_: datetime) -> int:
        async with d.databases.transaction(LogicalDb.AGENTS) as session:
            return await relay_once(session, "agents", d.bus.publish)

    async def beat_heartbeat(_: datetime) -> None:
        await heartbeat(d.redis, d.company_code, await d.store.lag_seconds())

    async def retention_plumbing(now: datetime) -> dict[str, int]:
        """D48 nightly: prune plumbing in every database, keep partitions ahead, archive and drop old ones."""
        r = d.core.retention
        out: dict[str, int] = {}
        for db, rules in (
            (LogicalDb.CRM, retention_rules(r)),
            (LogicalDb.AUTOMATION, automation_retention.plumbing_rules(r)),
        ):
            for rule in rules:
                out[f"{db.value}.{rule.table}"] = await prune_batches(d.databases, db, rule, batch=r.batch)
        agents = await agents_retention.run_retention(
            d.databases, r, today=now.astimezone(UTC).date(), store=d.archive, company=d.company_code
        )
        out.update({f"agents.{k}": v for k, v in agents.items()})
        return out

    return {
        "jobs.dispatch_due": dispatch_due,
        "relay.agents": relay_agents,
        "app.heartbeat": beat_heartbeat,
        "app.retention": retention_plumbing,
        **auto_jobs.periodic_jobs(d.automation),
        **crm_jobs.periodic_jobs(d.crm),
    }


_PLACEHOLDER_SECRETS: Final = frozenset({"", "dev-only-change-me", "CHANGE", "CHANGE_ME", "changeme"})
MIN_SECRET_CHARS: Final = 32


def check_secrets(env: Environment, secrets: Mapping[str, SecretStr]) -> None:
    """M1 (live test 18 Nov): these secrets sign website-session, unsubscribe and Telnyx callback tokens; with
    the code default anyone can forge them. Staging and production refuse to start while one is a placeholder
    or shorter than 32 characters (`openssl rand -hex 32`); local and test only warn."""
    plain = {name: value.get_secret_value() for name, value in secrets.items()}
    weak = sorted(n for n, v in plain.items() if v in _PLACEHOLDER_SECRETS or len(v) < MIN_SECRET_CHARS)
    if not weak:
        return
    if env in {Environment.STAGING, Environment.PRODUCTION}:
        raise RuntimeError(f"weak or default signing secrets: {', '.join(weak)} (set 32+ random characters)")
    log.warning("security.weak_secrets", settings=weak)


@asynccontextmanager
async def build(kicker: Kicker | None = None) -> AsyncIterator[Container]:  # noqa: PLR0915 - composition root
    """Every process builds the same container. `kicker` defaults to Celery (API, worker, voice)."""
    core, s, co = get_settings(), app_settings(), company()
    check_secrets(
        core.env,
        {
            "KX_SERVICE_TOKEN_SECRET": core.service_token_secret,
            "KX_AUTO_TELNYX__CALLBACK_SECRET": AutomationSettings().telnyx.callback_secret,
        },
    )
    async with AsyncExitStack() as stack:
        dbs = Databases(core)
        stack.push_async_callback(dbs.dispose)
        http = await stack.enter_async_context(
            httpx.AsyncClient(timeout=AutomationSettings().http_timeout_seconds)
        )
        redis = Redis.from_url(str(core.redis_url))
        stack.push_async_callback(redis.aclose)
        modes = core.modes()  # D41: raises at start-up on an impossible paid / free setting
        try:  # 18 Nov editable texts: the edits now, then every 30 s
            await texts_sync.refresh(dbs)
        except Exception:  # built-in wording until the CRM answers
            log.warning("texts.initial_load_failed", exc_info=True)
        refresher = asyncio.get_running_loop().create_task(texts_sync.refresh_loop(dbs))
        stack.callback(refresher.cancel)
        session = aioboto3.Session()
        st = core.storage  # D45: Cloudflare R2 through the S3 API
        s3 = await stack.enter_async_context(
            session.client(
                "s3",
                endpoint_url=st.endpoint_url,
                region_name=st.region,
                aws_access_key_id=st.access_key_id.get_secret_value() if st.access_key_id else None,
                aws_secret_access_key=st.secret_access_key.get_secret_value()
                if st.secret_access_key
                else None,
            )
        )

        # ---- job store (D25) + automation
        store = JobStore(dbs, kicker=kicker if kicker is not None else CeleryKicker())
        runner = JobRunner(store)
        scheduler = JobScheduler(store)
        crm_settings = SettingsService(ttl_seconds=30)
        async with dbs.transaction(LogicalDb.CRM) as crm_session:  # §8.1 settings live in the CRM
            business = await SettingsService(ttl_seconds=0).get(crm_session)
        check_letterhead(co, core.env, business.company_profile.model_dump())
        auto_s = AutomationSettings(  # init values win over KX_AUTO_* env for these two
            company_name=co.name,
            optout=OptOutSettings(
                ask_reason=business.optout_ask_reason,
                live_minutes=business.optout_live_minutes,
                unclear_answer_hours=business.optout_unclear_answer_hours,
            ),
        )
        unsubscribe_links = UnsubscribeLinks(
            core.service_token_secret.get_secret_value(), auto_s.public_base_url
        )
        ses = await stack.enter_async_context(session.client("sesv2", region_name=auto_s.ses.region))
        configured = configured_vendors(core, s, auto_s)
        _log_platform(modes, configured)

        async def scraping_enabled() -> bool:  # D14: v2 features are CRM settings
            async with dbs.transaction(LogicalDb.CRM) as crm_session:
                return (await crm_settings.get(crm_session)).feature_scraping

        jmap = JmapClient(http, auto_s.stalwart)
        direct_mail = _email_chain(ses, jmap, auto_s, modes, configured, co.code, alert=None)
        ops = OpsNotifier(redis, co.code, direct_mail, s.ops_alert_emails)  # D41 switch alerts
        email_sender = _email_chain(ses, jmap, auto_s, modes, configured, co.code, alert=ops)
        crm_deps = CrmDeps(
            uploader=S3PdfUploader(s3, st.bucket),  # D45 R2
            letterhead=Letterhead(**co.letterhead),
            renderer=_pdf_renderer(s, co.code),  # D37 Chromium -> WeasyPrint
            company_code=co.code,
            base_currency=core.base_currency,
            tokens=_token_issuer(core),  # D26 in-house login (RS256)
            seed_box=_seed_box(core),
            mailer=StaffCodeMailer([email_sender]),
            auth=staff_auth(core.auth, s.cors_origins, ip_header=core.trusted_client_ip_header),
            fx_source=OpenRatesFx(http, s.fx_rates_url) if s.fx_rates_auto else None,  # 18 Nov
        )
        media = S3MediaStore(s3, st.bucket, prefix="media/")
        whatsapp = WhatsAppClient(http, auto_s.whatsapp)
        telnyx = TelnyxCaller(http, auto_s.telnyx)  # D43
        limiter = TokenBucketLimiter(redis, co.code)
        spend = SpendMeter(  # D34 daily caps (OPEN_QUESTIONS #52)
            redis,
            co.code,
            SpendCaps(
                s.spend_ai_usd_per_day,
                s.spend_voice_minutes_per_day,
                s.spend_wa_templates_per_day,
                ai_usd_public=s.spend_ai_usd_per_day * s.spend_public_ai_share,  # M6 website sub-cap
            ),
            timezone=core.company_timezone,
        )
        kill_switch = KillSwitch(redis, co.code)
        outbound = OutboundService(
            databases=dbs,
            settings=auto_s,
            grants=GrantConsumerImpl(dbs, crm_deps),
            kill_switch=kill_switch,
            limiter=limiter,
            scheduler=scheduler,
            media=quote_pdf_store(s3, st.bucket),
            whatsapp=whatsapp,
            email=email_sender,
            unsubscribe_link=unsubscribe_links,
            template_spend=spend,  # D34 WhatsApp-template cap
        )
        # §14 (D21): staff reply from the CRM inbox and click-to-call, both through the company number
        crm_deps = replace(
            crm_deps,
            sender=outbound,
            caller=CallBridge(dbs, auto_s, telnyx, kill_switch),
            kill_switch=kill_switch,  # D35 ladder, set from the CRM admin screen
            throttle=RedisAuthThrottle(limiter, redis, co.code),  # M3 staff sign-in limits and lock-out
        )
        gateway = CrmGatewayImpl(dbs, crm_deps)
        voice = VoiceAlerts(dbs, auto_s, telnyx)
        ladder = ApprovalLadder(
            databases=dbs,
            settings=auto_s,
            approvals=ApprovalStatusReaderImpl(dbs),
            voice=voice,
            sender=outbound,
            scheduler=scheduler,
        )
        auto_handlers = EventHandlers(
            databases=dbs, settings=auto_s, sender=outbound, ladder=ladder, scheduler=scheduler
        )
        bus = EventBus(store)

        # ---- AI agents
        model_id, instruction = EMBEDDING_MODELS[modes.embeddings]  # D47 BGE-M3 (or Qwen3)
        embedder = HttpEmbedder(
            http,
            s.embedding_url or "http://embeddings:80",
            model_id,
            api_key=s.embedding_api_key.get_secret_value() if s.embedding_api_key else None,
            timeout_seconds=s.embedding_index_timeout_seconds,
            query_instruction=instruction,
        )
        checkpointer = await stack.enter_async_context(
            open_checkpointer(core.agents_dsn, setup=not core.db_pgbouncer, pgbouncer=core.db_pgbouncer)
        )

        agent_service = AgentService(
            databases=dbs,
            gateway=gateway,
            models=_model_router(s, modes, configured),
            knowledge=KnowledgeBase(dbs, embedder, query_timeout_seconds=s.embedding_query_timeout_seconds),
            checkpointer=checkpointer,
            tracer=Tracer(  # D46 AI audit trail: Phoenix (or Langfuse)
                s.ai_trace_endpoint, s.phoenix_project, core.env.value, headers=s.ai_trace_headers or None
            ),
            live_minutes=business.optout_live_minutes,
            company=co.code,
            on_spend=partial(spend.add, "ai_usd"),  # D34 daily AI cap
            budget_left=partial(spend.left, "ai_usd"),
            on_public_spend=partial(spend.add, "ai_usd_public"),  # M6 the website's own sub-cap
            public_budget_left=partial(spend.left, "ai_usd_public"),
            price_second_opinion=s.price_second_opinion,  # M7 layer 3
        )
        cms_key = s.cms_api_key.get_secret_value() if s.cms_api_key else None
        indexer = KnowledgeIndexer(dbs, embedder, PayloadContentSource(http, s.cms_url, cms_key))
        agents = AgentHandlers(
            databases=dbs,
            service=agent_service,
            sender=outbound,
            addresses=ThreadAddressBook(dbs),
            indexer=indexer,
            checkpointer=checkpointer,
            burst=ThreadBurst(dbs) if s.reply_merge_seconds > 0 else None,  # merge quick buyer messages
            merge_seconds=s.reply_merge_seconds,
        )
        agent_handlers = agents.handlers()

        # ---- CRM jobs + event routing
        crm_rt = crm_jobs.CrmRuntime(dbs, crm_deps, bus.publish)
        _wire_events(
            runner,
            bus,
            crm_rt,
            agent_handlers,
            frozenset(auto_handlers.registry()),
            agent_last_resort=agents.last_resort,
        )
        runner.register(
            TRANSLATE_TEXT, JobSpec(partial(texts_sync.translate_text, agent_service.runner.models, dbs))
        )
        bus.route(ev.MESSAGE_TEXT_CHANGED, TRANSLATE_TEXT)
        writer = PayloadProductWriter(http, s.cms_url, cms_key) if cms_key else None
        runner.register(CMS_PRODUCT_DRAFT, JobSpec(partial(cms_product_draft, writer, dbs)))
        bus.route(ev.CATALOGUE_PRODUCT_CONTENT, CMS_PRODUCT_DRAFT)

        auto_rt = AutomationRuntime(
            databases=dbs,
            settings=auto_s,
            outbound=outbound,
            ladder=ladder,
            pause_alerts=PauseAlerts(auto_s, voice, outbound),
            followups=FollowUps(dbs, auto_s, outbound),
            handlers=auto_handlers,
            scraper=PriceScraper(
                databases=dbs, http=http, sources=auto_s.price_sources, timezone=core.company_timezone
            ),
            whatsapp_media=whatsapp,
            media=media,
            inbound_mail=S3MediaStore(s3, st.bucket),  # keys carry their `inbound-mail/` prefix
            publish=bus.publish,
            scraping_enabled=scraping_enabled,
            mail_poller=MailboxPoller(jmap, auto_s.stalwart.poll_batch) if auto_s.stalwart.enabled else None,
            scheduler=scheduler,
        )
        auto_jobs.register_jobs(runner, auto_rt)
        webhook_deps = WebhookDeps(
            databases=dbs, settings=auto_s, scheduler=scheduler, voice=voice, media=media
        )
        archive = S3MediaStore(s3, st.bucket) if core.retention.archive else None
        periodic = _periodic(
            _PeriodicDeps(store, dbs, bus, auto_rt, crm_rt, http, s, co.code, redis, core, archive)
        )
        routes = voice_routes(modes, configured)
        slots = VoiceSlots(redis, co.code, s.voice_max_concurrent_free)

        def voice_switch(field: str, from_vendor: str, to_vendor: str) -> None:
            task = asyncio.get_running_loop().create_task(ops(field, from_vendor, to_vendor))
            _BACKGROUND.add(task)
            task.add_done_callback(_BACKGROUND.discard)

        yield Container(
            core=core,
            app=s,
            automation_settings=auto_s,
            databases=dbs,
            http=http,
            jobs=store,
            runner=runner,
            periodic=periodic,
            bus=bus,
            automation_runtime=auto_rt,
            webhook_deps=webhook_deps,
            unsubscribe_deps=UnsubscribeDeps(dbs, auto_s, core.service_token_secret.get_secret_value()),
            crm_deps=crm_deps,
            agent_service=agent_service,
            website_deps=_website(
                core,
                s,
                dbs=dbs,
                http=http,
                service=agent_service,
                gateway=gateway,
                spend=spend,
                limiter=limiter,
                kill_switch=kill_switch,
                routes=routes,
                slots=slots,
            ),
            spend=spend,
            redis=redis,
            modes=modes,
            knowledge_indexer=indexer,
            voice_routes=routes,
            voice_switch=voice_switch,
        )


def quote_pdf_store(s3: Any, bucket: str) -> S3MediaStore:
    """The store the sender reads quote PDFs from. The CRM uploads them at their full key (`pdf_key`:
    `{company}/quotes/...`), so no prefix: with the `media/` prefix of the inbound-media store every approved
    quote failed with 404 and was never sent (D-2, live test 18 Nov)."""
    return S3MediaStore(s3, bucket)


def _seed_box(core: CoreSettings) -> SeedBox:
    """TOTP seeds are encrypted at rest with KX_AUTH__ENCRYPTION_KEY. Local runs and tests may start without
    one (a process-local key: authenticator enrolments do not survive a restart); staging and production
    refuse to start, because a lost key forces every staff member to enrol again."""
    if core.auth.encryption_key is not None:
        return SeedBox(core.auth.encryption_key.get_secret_value())
    if core.env in {Environment.STAGING, Environment.PRODUCTION}:
        raise RuntimeError("KX_AUTH__ENCRYPTION_KEY is required outside local and test")
    log.warning("auth_dev_seed_key", detail="KX_AUTH__ENCRYPTION_KEY unset; using a process-local key")
    return SeedBox(SeedBox.new_key())


def _token_issuer(core: CoreSettings) -> TokenIssuer:
    """Every API task must sign with the same key (KX_JWT_PRIVATE_KEY from Secrets Manager); a generated
    key is only for one local process."""
    if core.jwt_private_key is None and core.env in {Environment.STAGING, Environment.PRODUCTION}:
        raise RuntimeError("KX_JWT_PRIVATE_KEY is required outside local and test")
    return TokenIssuer.from_settings(core)


def staff_auth(
    auth: AuthSettings, cors_origins: tuple[str, ...], *, ip_header: str | None = None
) -> AuthSettings:
    """M2: /v1/auth accepts browser calls only from the CRM origins; unless KX_AUTH__CRM_ORIGINS says
    otherwise these are the credentialed CORS origins (KX_APP_CORS_ORIGINS). M3: the per-IP limits read
    the client IP like the website does (KX_TRUSTED_CLIENT_IP_HEADER)."""
    update: dict[str, object] = {}
    if auth.crm_origins is None:
        update["crm_origins"] = cors_origins
    if auth.client_ip_header is None and ip_header is not None:
        update["client_ip_header"] = ip_header
    return auth.model_copy(update=update) if update else auth


_BACKGROUND: set[asyncio.Task[None]] = set()  # alert tasks started from LiveKit callbacks (kept referenced)
PUBLIC_BUCKETS = {"ip": Bucket(30, 30), "session": Bucket(12, 12)}  # OPEN_QUESTIONS #61


def public_buckets(s: AppSettings) -> dict[str, Bucket]:
    """D39 per-IP / per-session request limits, plus M6: website AI turns per minute from all visitors
    together and new sessions per client IP per day (a token bucket refilling over 24 hours)."""
    per_day = s.public_sessions_per_ip_per_day
    return {
        **PUBLIC_BUCKETS,
        "global": Bucket(s.public_turns_per_minute, s.public_turns_per_minute),
        "ip_sessions": Bucket(per_day, per_day / 1440),
    }


def _website(
    core: CoreSettings,
    s: AppSettings,
    *,
    dbs: Databases,
    http: httpx.AsyncClient,
    service: AgentService,
    gateway: CrmGatewayImpl,
    spend: SpendMeter,
    limiter: TokenBucketLimiter,
    kill_switch: KillSwitch,
    routes: dict[str, VoiceRoute],
    slots: VoiceSlots,
) -> WebsiteDeps:
    """§6 public website API (D53): Turnstile + per-IP/per-session limits + the daily AI spend cap; voice also
    needs the voice-minute cap, the kill switch below `text_only` and a free-version call slot (D52)."""
    if s.turnstile_secret_key is None and core.env in {Environment.STAGING, Environment.PRODUCTION}:
        raise RuntimeError("KX_APP_TURNSTILE_SECRET_KEY is required outside local and test")
    livekit = (
        LiveKitConfig(s.livekit_url, s.livekit_api_key, s.livekit_api_secret.get_secret_value())
        if s.livekit_url and s.livekit_api_key and s.livekit_api_secret
        else None
    )

    async def ai_budget_left() -> bool:
        return await spend.left("ai_usd")

    async def voice_allowed() -> bool:  # D34 voice-minute cap, D35 kill switch at `text_only`, D52 slots
        return (
            await spend.left("voice_minutes")
            and await spend.left("ai_usd_public")  # M6: a voice call spends the website's AI share
            and not await kill_switch.engaged("voice")
            and await slots.available()
        )

    return WebsiteDeps(
        databases=dbs,
        service=service,
        gateway=gateway,
        secret=core.service_token_secret.get_secret_value(),
        human_check=(
            TurnstileVerifier(http, s.turnstile_secret_key.get_secret_value())
            if s.turnstile_secret_key
            else None
        ),
        limits=PublicLimiter(limiter, public_buckets(s)),
        ai_budget_left=ai_budget_left,
        voice_allowed=voice_allowed,
        voice=VoiceRouter(routes),
        livekit=livekit,
        trusted_ip_header=core.trusted_client_ip_header,
    )


def _email_chain(  # noqa: PLR0917 - one call site; every argument is a different service
    ses: Any,
    jmap: JmapClient,
    auto_s: AutomationSettings,
    modes: Modes,
    configured: Collection[str],
    company_code: str,
    *,
    alert: Callable[[str, str, str], Awaitable[None]] | None,
) -> EmailChain:
    """D44 email field: Stalwart (JMAP, relaying through SES) -> the SES v2 API, in the platform order."""
    vendors: dict[str, EmailVendor] = {"ses": EmailSender(ses, auto_s.ses)}
    if auto_s.stalwart.enabled:
        vendors["stalwart"] = StalwartSender(jmap, auto_s.ses)
    email_order = order(Field.EMAIL, modes, configured)

    async def current_order() -> tuple[str, ...]:
        return email_order

    return EmailChain(vendors, FallbackChain("email", company_code, alert=alert), current_order)


def _pdf_renderer(s: AppSettings, company_code: str) -> QuoteRenderer:
    """D37: headless Chromium on the worker, WeasyPrint as the backup (every switch is counted). Without the
    Chromium binary (a developer laptop) WeasyPrint alone, said once at start-up."""
    engines: list[PdfEngine] = []
    if s.pdf_renderer == "chromium":
        if pathlib.Path(s.chromium_path).exists():
            engines.append(ChromiumEngine(s.chromium_path))
        else:
            log.warning("pdf_chromium_missing", path=s.chromium_path, detail="using WeasyPrint only")
    engines.append(WeasyPrintEngine())
    return QuoteRenderer(engines=engines, company=company_code)
