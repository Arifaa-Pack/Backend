"""Deploy-time settings (env vars; Coolify encrypted variables on the server). Business rules live in
crm.settings, not here. The paid / free version per field (D41) is `platform_plan` + `platform`."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import BaseModel, Field, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from kx.core.platform import Modes, resolve_modes

# str, not PostgresDsn: unix-socket DSNs (host=/path) are valid for asyncpg but fail URL host checks.
AsyncPgDsn = Annotated[str, Field(pattern=r"^postgresql\+asyncpg://")]


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class LogicalDb(StrEnum):
    """The logical databases the Python packages own (each with its own DSN, so any one can move to its own
    server, D48). The CMS database belongs to Payload and Phoenix has its own container."""

    CRM = "crm"
    AUTOMATION = "automation"
    AGENTS = "agents"


class RateLimit(BaseModel):
    """A token bucket: `burst` requests at once, then `per_minute` (env: KX_AUTH__RATE_LOGIN_IP__BURST=20)."""

    burst: int = Field(ge=1)
    per_minute: float = Field(gt=0)


class AuthSettings(BaseModel):
    """In-house staff login (OPEN_QUESTIONS #50 for the values the documents do not give)."""

    access_ttl_seconds: int = Field(default=900, ge=60)  # short-lived access tokens (Recommendation)
    refresh_ttl_seconds: int = Field(default=43_200, ge=600)  # 12 h sliding, rotated on every use
    # M3: wrong passwords / codes are counted per account AND client IP: one attacker locks only their own
    # address out, never the staff member everywhere. The account-wide lock stays as a much higher backstop.
    max_failed_attempts: int = Field(default=5, ge=1)  # per account + IP, then that IP is locked out
    account_lock_failures: int = Field(default=50, ge=1)  # from all IPs together, then the account is locked
    lockout_minutes: int = Field(default=15, ge=1)
    # M3: Redis token buckets (per client IP and per account) on the unauthenticated auth routes
    rate_login_ip: RateLimit = RateLimit(burst=20, per_minute=10)
    rate_login_account: RateLimit = RateLimit(burst=10, per_minute=2)
    rate_mfa_send_ip: RateLimit = RateLimit(burst=5, per_minute=1)
    rate_mfa_send_account: RateLimit = RateLimit(burst=3, per_minute=0.5)
    rate_reset_start_ip: RateLimit = RateLimit(burst=5, per_minute=1)
    rate_reset_start_account: RateLimit = RateLimit(burst=3, per_minute=0.05)  # at most 3 resets an hour
    rate_reset_finish_ip: RateLimit = RateLimit(burst=10, per_minute=2)
    rate_reset_finish_account: RateLimit = RateLimit(burst=10, per_minute=0.2)
    reset_max_wrong_codes: int = Field(default=5, ge=1)  # wrong reset codes, then reset is locked (lockout)
    code_ttl_seconds: int = Field(default=600, ge=60)  # email codes and login challenges
    code_max_tries: int = Field(default=5, ge=1)
    encryption_key: SecretStr | None = None  # Fernet key for TOTP seeds (KX_AUTH__ENCRYPTION_KEY)
    cookie_secure: bool = True
    cookie_domain: str | None = None
    totp_issuer: str = "Kairox CRM"
    # M2: browser origins (the CRM screens) that may call /v1/auth; a request from any other Origin is
    # refused. None = the app fills it from its credentialed CORS origins (KX_APP_CORS_ORIGINS).
    crm_origins: tuple[str, ...] | None = None
    # M3: the header carrying the client IP behind Cloudflare (the app copies KX_TRUSTED_CLIENT_IP_HEADER)
    client_ip_header: str | None = None


Plan = Literal["paid", "free"]


class PlatformModes(BaseModel):
    """D41 per-field overrides (`KX_PLATFORM__STT=free` …); None = the `KX_PLATFORM_PLAN` preset.
    Resolved and validated by `kx.core.platform.resolve_modes` (`CoreSettings.modes()`)."""

    stt: Plan | None = None
    tts: Plan | None = None
    email: Plan | None = None
    llm: Plan | None = None  # 'free' is refused: no free model
    calls: Plan | None = None  # 'free' is refused until an Asterisk adapter exists
    whatsapp: Plan | None = None  # 'free' is refused: Meta only
    llm_standby: str = "none"  # none | gemini (paid, used only when Claude fails; D27, OPEN #74)
    embeddings: str = "bge-m3"  # bge-m3 | qwen3 (both free; a change needs `kx reindex`)
    ai_trace: str = "phoenix"  # phoenix | langfuse (both free, both OTLP)


class StorageSettings(BaseModel):
    """D45: Cloudflare R2 through the S3 API (`KX_STORAGE__*`). One bucket, prefixes per use: `media/`,
    `quotes/`, `inbound-mail/`, `archive/` (and WAL-G's `backups/`, configured on the database server)."""

    endpoint_url: str | None = None  # https://<account>.r2.cloudflarestorage.com ; None = AWS default chain
    region: str = "auto"  # R2 takes "auto"; location hint is chosen when the bucket is made (OPEN #77)
    access_key_id: SecretStr | None = None
    secret_access_key: SecretStr | None = None
    bucket: str = "kx-local"


class RetentionSettings(BaseModel):
    """D48 nightly retention of the Interactions tier (`KX_RETENTION__*`, OPEN #73). Business data follows
    the §22 rules in `kx.crm.services.retention`, not these values."""

    plumbing_days: int = Field(
        default=30, ge=7
    )  # outbox / inbox / webhook receipts / idempotency / done jobs
    failed_job_days: int = Field(default=90, ge=7)
    agent_runs_months: int = Field(default=13, ge=1)
    partition_hot_months: int = Field(default=6, ge=1)
    archive: bool = True  # write rows to R2 before they are deleted or a partition is dropped
    batch: int = Field(default=5000, ge=100)


class CoreSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KX_", env_nested_delimiter="__", extra="ignore", env_ignore_empty=True
    )

    env: Environment = Environment.LOCAL
    company_code: str = Field(default="pack", pattern=r"^[a-z][a-z0-9_]{1,20}$")
    company_timezone: str = "Asia/Dubai"
    base_currency: str = Field(default="AED", min_length=3, max_length=3)

    crm_dsn: AsyncPgDsn = "postgresql+asyncpg://crm_app:dev@localhost:5432/crm"
    automation_dsn: AsyncPgDsn = "postgresql+asyncpg://automation_app:dev@localhost:5432/automation"
    agents_dsn: AsyncPgDsn = "postgresql+asyncpg://agents_app:dev@localhost:5432/agents"
    db_pool_size: int = 5  # bench R1: 106 connections at 10 + 10; 5 + 5 per process and DB (D48)
    db_max_overflow: int = 5
    db_statement_timeout_ms: int = 15_000
    # PgBouncer in transaction mode (D48): no server-side prepared-statement caches, and search_path /
    # statement_timeout come from `ALTER ROLE … SET` (db_bootstrap.sh) instead of the connect packet
    db_pgbouncer: bool = False

    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")

    # Staff login (11 Nov, D26: in-house, RS256 tokens; Cognito removed)
    jwt_issuer: str = "http://localhost:8000"
    jwt_audience: str = "kx-crm"
    jwt_private_key: SecretStr | None = None  # RSA PEM (encrypted env var); a dev key is generated if unset
    jwt_key_id: str = "kx-dev-1"
    jwks_url: str | None = None  # only to verify tokens from another issuer; default: our own public key
    auth: AuthSettings = AuthSettings()
    service_token_secret: SecretStr = SecretStr("dev-only-change-me")  # signs website + unsubscribe tokens

    platform_plan: Plan = "paid"  # D41 preset: KX_PLATFORM_PLAN=paid|free
    platform: PlatformModes = PlatformModes()  # D41 overrides: KX_PLATFORM__<FIELD>
    storage: StorageSettings = StorageSettings()
    retention: RetentionSettings = RetentionSettings()

    # D53: the client IP of a public request. Set to "CF-Connecting-IP" on the server (every web request comes
    # through Cloudflare); unset = the direct peer (uvicorn's proxy headers handling)
    trusted_client_ip_header: str | None = None

    log_level: str = "INFO"
    log_json: bool = True

    def modes(self) -> Modes:
        """The resolved paid / free version per field; raises PlatformConfigError on an impossible setting."""
        p = self.platform
        return resolve_modes(
            self.platform_plan,
            {
                "stt": p.stt,
                "tts": p.tts,
                "email": p.email,
                "llm": p.llm,
                "calls": p.calls,
                "whatsapp": p.whatsapp,
            },
            llm_standby=p.llm_standby,
            embeddings=p.embeddings,
            ai_trace=p.ai_trace,
        )

    def dsn_for(self, db: LogicalDb) -> str:
        return str(
            {
                LogicalDb.CRM: self.crm_dsn,
                LogicalDb.AUTOMATION: self.automation_dsn,
                LogicalDb.AGENTS: self.agents_dsn,
            }[db]
        )


@lru_cache(maxsize=1)
def get_settings() -> CoreSettings:
    return CoreSettings()
