"""In-house staff login over HTTP (11 Nov plan WP21, D26): password -> TOTP or email code -> RS256 tokens,
refresh rotation and reuse detection, lock-out, break-glass alert, admin user management.

Real JWT verification: no `current_principal` override, so the tokens the login issues are the ones the
routes check.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from conftest import Seed
from fastapi import FastAPI
from sqlalchemy import select

from kx.core import events as ev
from kx.core.auth import TokenIssuer
from kx.core.errors import install_problem_handlers
from kx.core.passwords import SeedBox, totp_now
from kx.core.settings import AuthSettings, LogicalDb, RateLimit, get_settings
from kx.crm.api import build_router, session_guard
from kx.crm.deps import CrmDeps
from kx.crm.models import AuditEvent, OutboxEvent, StaffCredential
from kx.crm.services import auth as svc
from kx.crm.services.journal import Actor
from kx.crm.throttle import MemoryThrottle

pytestmark = pytest.mark.db


@dataclass
class FakeMailer:
    sent: list[tuple[str, str, str]] = field(default_factory=list)

    async def send_code(self, to_email: str, subject: str, body: str) -> None:
        self.sent.append((to_email, subject, body))

    def last_code(self) -> str:
        return self.sent[-1][2].split("code is ", 1)[1][:6]


@dataclass
class Clock:
    # starts in the past, so tokens issued after a TOTP step forward never carry a future `iat`
    at: datetime = field(default_factory=lambda: datetime.now(UTC) - timedelta(minutes=5))

    def __call__(self) -> datetime:
        return self.at


@dataclass
class Env:
    client: httpx.AsyncClient
    deps: CrmDeps
    mailer: FakeMailer
    clock: Clock
    databases: Any


@pytest.fixture
async def env(
    databases: Any, seed: Seed, deps: CrmDeps, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Env]:
    monkeypatch.setattr(session_guard, "CACHE_SECONDS", 0.0)  # L1: every request sees the session at once
    mailer, clock = FakeMailer(), Clock()
    auth_deps = replace(
        deps,
        tokens=TokenIssuer.from_settings(get_settings()),
        seed_box=SeedBox(SeedBox.new_key()),
        mailer=mailer,
        auth=AuthSettings(cookie_secure=False),
        clock=clock,
        throttle=MemoryThrottle(clock=lambda: clock().timestamp()),  # M3 limits follow the test clock
    )
    app = FastAPI()
    app.include_router(build_router(databases, auth_deps), prefix="/v1")
    install_problem_handlers(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        yield Env(client, auth_deps, mailer, clock, databases)


async def new_user(env: Env, role: str = "admin", *, break_glass: bool = False) -> tuple[str, str, uuid.UUID]:
    email = f"staff-{uuid.uuid4().hex[:10]}@test.ae"
    async with env.databases.transaction(LogicalDb.CRM) as s:
        user, password = await svc.create_user(
            s,
            env.deps,
            email=email,
            full_name="Test Staff",
            role=role,
            break_glass=break_glass,
            actor=Actor.system("test"),
        )
    return email, password, user.id


async def email_login(env: Env, email: str, password: str) -> dict[str, Any]:
    challenge = (await env.client.post("/v1/auth/login", json={"email": email, "password": password})).json()
    assert (await env.client.post("/v1/auth/mfa/email/send", json=challenge)).status_code == 204
    body = {"challenge_token": challenge["challenge_token"], "code": env.mailer.last_code()}
    r = await env.client.post("/v1/auth/mfa/email/verify", json=body)
    assert r.status_code == 200, r.text
    result: dict[str, Any] = r.json()
    return result


def bearer(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def enrol_totp(env: Env, tokens: dict[str, Any]) -> str:
    enrol = (await env.client.post("/v1/auth/totp/enrol", headers=bearer(tokens))).json()
    assert enrol["otpauth_uri"].startswith("otpauth://totp/")
    code = totp_now(enrol["secret"], env.clock())
    r = await env.client.post("/v1/auth/totp/confirm", json={"code": code}, headers=bearer(tokens))
    assert r.status_code == 204
    return str(enrol["secret"])


async def totp_login(env: Env, email: str, password: str, secret: str) -> dict[str, Any]:
    challenge = (await env.client.post("/v1/auth/login", json={"email": email, "password": password})).json()
    assert challenge["methods"] == ["totp", "email"] and challenge["totp_enrolled"]
    env.clock.at += timedelta(seconds=30)  # next TOTP step (the enrolment code cannot be replayed)
    body = {"challenge_token": challenge["challenge_token"], "code": totp_now(secret, env.clock())}
    r = await env.client.post("/v1/auth/mfa/totp", json=body)
    assert r.status_code == 200, r.text
    result: dict[str, Any] = r.json()
    return result


async def test_email_code_then_totp_gives_mfa(env: Env) -> None:
    email, password, user_id = await new_user(env)

    # first sign-in: only the email code is on offer; the session has no MFA and must change the password
    first = (await env.client.post("/v1/auth/login", json={"email": email, "password": password})).json()
    assert first["methods"] == ["email"] and not first["totp_enrolled"]
    tokens = await email_login(env, email, password)
    assert tokens["mfa"] is False and tokens["amr"] == ["pwd", "email"]
    assert tokens["must_change_password"] is True and tokens["user"]["id"] == str(user_id)
    me = await env.client.get("/v1/auth/me", headers=bearer(tokens))
    assert me.status_code == 200 and me.json()["email"] == email

    # L7: until the one-time password is changed only /v1/auth/* works
    r = await env.client.get("/v1/users", headers=bearer(tokens))
    assert r.status_code == 403 and r.json()["detail"] == session_guard.CHANGE_FIRST

    new_password = "a-much-longer-passphrase-2026"
    r = await env.client.post(
        "/v1/auth/password",
        json={"current_password": password, "new_password": new_password},
        headers=bearer(tokens),
    )
    assert r.status_code == 204

    # the session in use survives its own password change (every other one ends)
    r = await env.client.get("/v1/users", headers=bearer(tokens))
    assert r.status_code == 200  # reading the team is fine
    # an email-code session cannot approve or manage users (require_human_mfa)
    r = await env.client.post(
        "/v1/users",
        json={"email": "x@test.ae", "full_name": "X", "role": "sales"},
        headers=bearer(tokens),
    )
    assert r.status_code == 403

    secret = await enrol_totp(env, tokens)
    mfa = await totp_login(env, email, new_password, secret)
    assert mfa["mfa"] is True and mfa["amr"] == ["pwd", "otp"] and mfa["must_change_password"] is False

    # an MFA admin creates a salesperson; the one-time password is returned once
    r = await env.client.post(
        "/v1/users",
        json={"email": f"new-{uuid.uuid4().hex[:8]}@test.ae", "full_name": "New Sales", "role": "sales"},
        headers=bearer(mfa),
    )
    assert r.status_code == 201, r.text
    assert len(r.json()["temporary_password"]) >= 12 and r.json()["user"]["role"] == "sales"


async def test_an_email_code_session_cannot_replace_an_active_authenticator(env: Env) -> None:
    """D4: with only the mailbox (email-code sign-in, mfa=false) nobody may swap the authenticator and so gain
    approval rights. A TOTP session may, and the user is told by email. After an admin reset, the email-code
    session may enrol again (lost-phone recovery)."""
    email, password, user_id = await new_user(env, role="sales")
    first = await email_login(env, email, password)
    secret = await enrol_totp(env, first)  # first enrolment: allowed from the email-code session

    # an attacker with the mailbox: email-code session -> replacing the active seed is refused, seed untouched
    attacker = await email_login(env, email, password)
    assert attacker["mfa"] is False
    r = await env.client.post("/v1/auth/totp/enrol", headers=bearer(attacker))
    assert r.status_code == 403, r.text
    still = await totp_login(env, email, password, secret)  # the real authenticator still works
    assert still["mfa"] is True

    # the owner, signed in with TOTP, may replace it; the replacement is announced by email
    sent_before = len(env.mailer.sent)
    r = await env.client.post("/v1/auth/totp/enrol", headers=bearer(still))
    assert r.status_code == 200, r.text
    assert len(env.mailer.sent) == sent_before + 1 and env.mailer.sent[-1][0] == email
    assert "being replaced" in env.mailer.sent[-1][1]

    # lost phone: an admin resets MFA, then the email-code session may enrol a new authenticator
    async with env.databases.transaction(LogicalDb.CRM) as s:
        await svc.reset_mfa(s, env.deps, user_id, Actor.system("test"))
    after_reset = await email_login(env, email, password)
    assert (await env.client.post("/v1/auth/totp/enrol", headers=bearer(after_reset))).status_code == 200


async def _mfa_session(env: Env, role: str) -> tuple[dict[str, Any], uuid.UUID]:
    email, password, user_id = await new_user(env, role=role)
    first = await email_login(env, email, password)
    new_password = "a-much-longer-passphrase-2026"  # L7: the one-time password is changed first
    r = await env.client.post(
        "/v1/auth/password",
        json={"current_password": password, "new_password": new_password},
        headers=bearer(first),
    )
    assert r.status_code == 204
    secret = await enrol_totp(env, first)
    return await totp_login(env, email, new_password, secret), user_id


async def test_only_the_md_manages_md_and_nobody_changes_their_own_role(env: Env) -> None:
    """D12(a), decided 17 Nov: the MD role reads cost data, so only an MD may create an MD, give or remove the
    role, deactivate an MD or reset an MD's authenticator; nobody changes their own role."""
    admin, admin_id = await _mfa_session(env, "admin")
    md, _ = await _mfa_session(env, "md")
    _, _, sales_id = await new_user(env, role="sales")
    _, _, other_md_id = await new_user(env, role="md")
    new = {"full_name": "New MD", "role": "md"}

    async def create(tokens: dict[str, Any]) -> int:
        body = {**new, "email": f"md-{uuid.uuid4().hex[:8]}@test.ae"}
        return (await env.client.post("/v1/users", json=body, headers=bearer(tokens))).status_code

    async def patch(tokens: dict[str, Any], user_id: uuid.UUID, body: dict[str, Any]) -> int:
        return (await env.client.patch(f"/v1/users/{user_id}", json=body, headers=bearer(tokens))).status_code

    async def reset(tokens: dict[str, Any], user_id: uuid.UUID) -> int:
        return (await env.client.post(f"/v1/users/{user_id}/reset-mfa", headers=bearer(tokens))).status_code

    assert await create(admin) == 403 and await create(md) == 201
    assert await patch(admin, sales_id, {"role": "md"}) == 403  # an admin cannot hand out the MD role
    assert await patch(admin, other_md_id, {"role": "sales"}) == 403  # ... or take it away
    assert await patch(admin, other_md_id, {"is_active": False}) == 403  # ... or switch an MD off
    assert await reset(admin, other_md_id) == 403  # ... or reset an MD's authenticator
    assert await patch(admin, admin_id, {"role": "manager"}) == 403  # nobody changes their own role
    assert await patch(admin, sales_id, {"role": "manager"}) == 200  # ordinary role changes still work
    assert await patch(md, sales_id, {"role": "md"}) == 200
    assert await reset(md, other_md_id) == 200


async def test_wrong_password_locks_after_five(env: Env) -> None:
    email, password, user_id = await new_user(env)
    for _ in range(5):
        r = await env.client.post("/v1/auth/login", json={"email": email, "password": "wrong-password"})
        assert r.status_code == 401
    r = await env.client.post("/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 429  # locked even with the right password

    env.clock.at += timedelta(minutes=16)
    r = await env.client.post("/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    async with env.databases.transaction(LogicalDb.CRM) as s:
        actions = (
            (await s.execute(select(AuditEvent.action).where(AuditEvent.entity_id == str(user_id))))
            .scalars()
            .all()
        )
    assert "auth.locked" in actions and actions.count("auth.login_failed") == 4

    # an unknown address answers exactly like a wrong password
    r = await env.client.post("/v1/auth/login", json={"email": "nobody@test.ae", "password": password})
    assert r.status_code == 401 and r.json()["detail"] == svc.GENERIC


async def test_refresh_rotates_and_reuse_ends_every_session(env: Env) -> None:
    email, password, _ = await new_user(env, "sales")
    tokens = await email_login(env, email, password)
    old = env.client.cookies.get("kx_refresh")
    assert old

    rotated = await env.client.post("/v1/auth/refresh")
    assert rotated.status_code == 200
    new = env.client.cookies.get("kx_refresh")
    assert new and new != old

    # the stolen old token comes back: refused, and the new one stops working too
    env.client.cookies.clear()
    r = await env.client.post("/v1/auth/refresh", json={"refresh_token": old})
    assert r.status_code == 401
    r = await env.client.post("/v1/auth/refresh", json={"refresh_token": new})
    assert r.status_code == 401
    assert tokens["access_token"]  # access tokens simply expire (15 minutes)


async def test_codes_are_bounded_and_single_use(env: Env) -> None:
    email, password, _ = await new_user(env, "sales")
    challenge = (await env.client.post("/v1/auth/login", json={"email": email, "password": password})).json()
    await env.client.post("/v1/auth/mfa/email/send", json=challenge)
    code = env.mailer.last_code()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        body = {"challenge_token": challenge["challenge_token"], "code": wrong}
        assert (await env.client.post("/v1/auth/mfa/email/verify", json=body)).status_code in {401, 429}
    body = {"challenge_token": challenge["challenge_token"], "code": code}
    r = await env.client.post("/v1/auth/mfa/email/verify", json=body)
    assert r.status_code in {401, 429}  # the challenge is spent (or the account locked): sign in again

    # an expired challenge is refused
    env.clock.at += timedelta(minutes=20)
    challenge = (await env.client.post("/v1/auth/login", json={"email": email, "password": password})).json()
    await env.client.post("/v1/auth/mfa/email/send", json=challenge)
    env.clock.at += timedelta(minutes=11)
    body = {"challenge_token": challenge["challenge_token"], "code": env.mailer.last_code()}
    assert (await env.client.post("/v1/auth/mfa/email/verify", json=body)).status_code == 401


async def test_break_glass_sign_in_alerts_md_and_manager(env: Env, seed: Seed) -> None:
    email, password, user_id = await new_user(env, break_glass=True)
    await email_login(env, email, password)
    async with env.databases.transaction(LogicalDb.CRM) as s:
        notices = (
            (
                await s.execute(
                    select(OutboxEvent.payload).where(
                        OutboxEvent.aggregate_id == str(user_id), OutboxEvent.event_type == ev.STAFF_NOTICE
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(notices) == 1
    notified = {n["user_ref"] for n in notices[0]["notify"]}
    assert notified == {str(seed.md), str(seed.manager)}
    assert "Break-glass" in notices[0]["text"]


async def test_reset_password_by_email_code(env: Env) -> None:
    email, _, user_id = await new_user(env, "sales")
    r = await env.client.post("/v1/auth/password/reset/start", json={"email": email})
    assert r.status_code == 202
    code = env.mailer.last_code()
    r = await env.client.post("/v1/auth/password/reset/start", json={"email": "nobody@test.ae"})
    assert r.status_code == 202 and len(env.mailer.sent) == 1  # no hint about unknown addresses

    new_password = "reset-passphrase-for-sales"
    body = {"email": email, "code": code, "new_password": new_password}
    assert (await env.client.post("/v1/auth/password/reset/finish", json=body)).status_code == 204
    tokens = await email_login(env, email, new_password)
    assert tokens["must_change_password"] is False
    async with env.databases.transaction(LogicalDb.CRM) as s:
        cred = await s.get(StaffCredential, user_id)
        assert cred is not None and cred.password_hash.startswith("$argon2id$")


async def test_auth_routes_refuse_a_browser_origin_that_is_not_the_crm(env: Env) -> None:
    """M2: a script on the public website (same site as the API) cannot swap the refresh cookie for a token."""
    crm, www = "https://crm.example.ae", "https://www.example.ae"
    deps = replace(env.deps, auth=AuthSettings(cookie_secure=False, crm_origins=(crm,)))
    app = FastAPI()
    app.include_router(build_router(env.databases, deps), prefix="/v1")
    install_problem_handlers(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        email, password, _ = await new_user(env, "sales")
        login = await client.post("/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200
        assert (await client.post("/v1/auth/mfa/email/send", json=login.json())).status_code == 204
        body = {"challenge_token": login.json()["challenge_token"], "code": env.mailer.last_code()}
        signed = await client.post("/v1/auth/mfa/email/verify", json=body, headers={"Origin": crm})
        assert signed.status_code == 200
        assert "samesite=strict" in signed.headers["set-cookie"].lower()  # M2: Strict, not Lax

        stolen = await client.post("/v1/auth/refresh", headers={"Origin": www})
        assert stolen.status_code == 403 and "access_token" not in stolen.text
        assert (await client.post("/v1/auth/refresh", headers={"Origin": "null"})).status_code == 403
        assert (await client.post("/v1/auth/logout", headers={"Origin": www})).status_code == 403
        r = await client.post(
            "/v1/auth/login", json={"email": email, "password": password}, headers={"Origin": www}
        )
        assert r.status_code == 403
        assert (await client.post("/v1/auth/refresh", headers={"Origin": crm})).status_code == 200
        assert (
            await client.post("/v1/auth/refresh")
        ).status_code == 200  # non-browser clients send no Origin


@contextlib.asynccontextmanager
async def _client_with(env: Env, auth: AuthSettings) -> AsyncIterator[httpx.AsyncClient]:
    """An app with other sign-in settings (M3 limits), on the same database, mailer and clock."""
    deps = replace(env.deps, auth=auth, throttle=MemoryThrottle(clock=lambda: env.clock().timestamp()))
    app = FastAPI()
    app.include_router(build_router(env.databases, deps), prefix="/v1")
    install_problem_handlers(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        yield client


def _from(ip: str) -> dict[str, str]:
    return {"X-Forwarded-For": ip}  # the hop our proxy saw (kx.core.clientip)


async def test_wrong_passwords_lock_out_the_attacker_ip_not_the_staff_member(env: Env) -> None:
    """M3: five wrong passwords from one IP lock that IP out; the real person signs in from elsewhere."""
    email, password, user_id = await new_user(env, "manager")
    wrong = {"email": email, "password": "wrong-password"}
    for _ in range(5):
        r = await env.client.post("/v1/auth/login", json=wrong, headers=_from("203.0.113.9"))
        assert r.status_code == 401
    right = {"email": email, "password": password}
    attacker = await env.client.post("/v1/auth/login", json=right, headers=_from("203.0.113.9"))
    assert attacker.status_code == 429
    staff = await env.client.post("/v1/auth/login", json=right, headers=_from("198.51.100.7"))
    assert staff.status_code == 200
    async with env.databases.transaction(LogicalDb.CRM) as s:
        cred = await s.get(StaffCredential, user_id)
        assert cred is not None and cred.locked_until is None  # the account itself is not locked
        locked = (
            await s.execute(
                select(AuditEvent.after).where(
                    AuditEvent.entity_id == str(user_id), AuditEvent.action == "auth.locked"
                )
            )
        ).scalar_one()
    assert locked["scope"] == "ip" and locked["ip"] == "203.0.113.9"

    # an unknown address is locked out the same way (no hint which addresses exist)
    ghost = {"email": "ghost@test.ae", "password": "wrong-password"}
    codes = [
        (await env.client.post("/v1/auth/login", json=ghost, headers=_from("203.0.113.10"))).status_code
        for _ in range(6)
    ]
    assert codes == [401] * 5 + [429]


async def test_account_wide_lock_is_a_backstop_across_many_ips(env: Env) -> None:
    """M3: failures from many IPs together still lock the account (`account_lock_failures`)."""
    auth = AuthSettings(cookie_secure=False, account_lock_failures=6)
    async with _client_with(env, auth) as client:
        email, password, _ = await new_user(env, "sales")
        for n in range(6):
            body = {"email": email, "password": "nope-nope"}
            assert (
                await client.post("/v1/auth/login", json=body, headers=_from(f"192.0.2.{n}"))
            ).status_code == 401
        body = {"email": email, "password": password}
        assert (
            await client.post("/v1/auth/login", json=body, headers=_from("192.0.2.99"))
        ).status_code == 429


async def test_login_is_rate_limited_per_ip_and_per_account(env: Env) -> None:
    """M3: token buckets on login, the same answer for known and unknown addresses."""
    auth = AuthSettings(
        cookie_secure=False,
        rate_login_ip=RateLimit(burst=3, per_minute=0.01),
        rate_login_account=RateLimit(burst=4, per_minute=0.01),
    )
    async with _client_with(env, auth) as client:
        email, password, _ = await new_user(env, "sales")
        body = {"email": email, "password": password}

        async def login(ip: str, payload: dict[str, str] = body) -> int:
            return (await client.post("/v1/auth/login", json=payload, headers=_from(ip))).status_code

        assert [await login("192.0.2.1") for _ in range(4)] == [200, 200, 200, 429]  # per IP
        assert await login("192.0.2.2") == 200
        assert await login("192.0.2.3") == 429  # per account: 4 attempts used
        assert await login("192.0.2.4", {"email": "ghost@test.ae", "password": "whatever-pass"}) == 401
        env.clock.at += timedelta(hours=8)  # the buckets refill
        assert await login("192.0.2.1") == 200


async def test_mfa_email_send_is_rate_limited(env: Env) -> None:
    auth = AuthSettings(cookie_secure=False, rate_mfa_send_account=RateLimit(burst=2, per_minute=0.01))
    async with _client_with(env, auth) as client:
        email, password, _ = await new_user(env, "sales")
        challenge = (await client.post("/v1/auth/login", json={"email": email, "password": password})).json()
        sent = [(await client.post("/v1/auth/mfa/email/send", json=challenge)).status_code for _ in range(3)]
        assert sent == [204, 204, 429]


async def test_reset_one_live_code_capped_starts_and_a_lock_after_wrong_codes(env: Env) -> None:
    """M3: a new start ends the previous code; starts per account are capped; wrong codes lock recovery."""
    email, _, _ = await new_user(env, "sales")
    new_password = "a-brand-new-passphrase"

    async def start(address: str, ip: str = "192.0.2.50") -> int:
        r = await env.client.post("/v1/auth/password/reset/start", json={"email": address}, headers=_from(ip))
        return r.status_code

    async def finish(code: str, ip: str = "192.0.2.60") -> int:
        body = {"email": email, "code": code, "new_password": new_password}
        r = await env.client.post("/v1/auth/password/reset/finish", json=body, headers=_from(ip))
        return r.status_code

    assert await start(email) == 202
    first = env.mailer.last_code()
    assert await start(email, "192.0.2.51") == 202
    second = env.mailer.last_code()
    if first != second:
        assert await finish(first) == 401  # only the newest code is live
    assert await start(email, "192.0.2.52") == 202
    third = env.mailer.last_code()
    # at most 3 starts an hour per address (default); an unknown address answers exactly the same
    assert await start(email, "192.0.2.53") == 429
    assert [await start("ghost-reset@test.ae", f"192.0.2.{n}") for n in range(4)] == [202, 202, 202, 429]

    # wrong codes (old ones included) lock recovery for the lock-out time, even for the right code afterwards
    wrong = "000000" if third != "000000" else "111111"
    for n in range(5 - (first != second)):
        assert await finish(wrong, f"192.0.2.{n + 10}") == 401
    assert await finish(third) == 429
    env.clock.at += timedelta(hours=2)
    assert await start(email) == 202
    assert await finish(env.mailer.last_code()) == 204


async def _ready_session(env: Env, role: str = "sales") -> tuple[dict[str, Any], uuid.UUID, str]:
    """A signed-in user who has changed the one-time password (so ordinary routes work)."""
    email, password, user_id = await new_user(env, role=role)
    tokens = await email_login(env, email, password)
    r = await env.client.post(
        "/v1/auth/password",
        json={"current_password": password, "new_password": "a-much-longer-passphrase-2026"},
        headers=bearer(tokens),
    )
    assert r.status_code == 204
    return tokens, user_id, email


async def test_l1_signing_out_ends_the_access_token_too(env: Env) -> None:
    """L1 (security review 19 Nov): the access token used to stay valid for 15 minutes after sign-out."""
    tokens, _, _ = await _ready_session(env)
    assert (await env.client.get("/v1/deals", headers=bearer(tokens))).status_code == 200
    assert (await env.client.post("/v1/auth/logout", headers=bearer(tokens))).status_code in (200, 204)
    r = await env.client.get("/v1/deals", headers=bearer(tokens))
    assert r.status_code == 401 and r.json()["detail"] == session_guard.ENDED


async def test_l1_deactivation_and_a_role_change_end_the_token(env: Env) -> None:
    admin, _ = await _mfa_session(env, "admin")
    sales, sales_id, _ = await _ready_session(env)
    other, other_id, _ = await _ready_session(env)
    assert (await env.client.get("/v1/deals", headers=bearer(sales))).status_code == 200
    r = await env.client.patch(f"/v1/users/{sales_id}", json={"is_active": False}, headers=bearer(admin))
    assert r.status_code == 200
    assert (await env.client.get("/v1/deals", headers=bearer(sales))).status_code == 401
    r = await env.client.patch(f"/v1/users/{other_id}", json={"role": "manager"}, headers=bearer(admin))
    assert r.status_code == 200
    # the token still says "sales": it must not keep working with a role the user no longer has
    assert (await env.client.get("/v1/deals", headers=bearer(other))).status_code == 401


async def test_l7_a_session_has_an_absolute_end_and_a_locked_account_cannot_refresh(env: Env) -> None:
    tokens, user_id, _ = await _ready_session(env)
    async with env.databases.transaction(LogicalDb.CRM) as s:
        first_end = (
            (
                await s.execute(
                    select(svc.AuthSession.expires_at)
                    .where(svc.AuthSession.user_id == user_id)
                    .order_by(svc.AuthSession.created_at)
                )
            )
            .scalars()
            .first()
        )
    env.clock.at += timedelta(hours=1)
    assert (await env.client.post("/v1/auth/refresh")).status_code == 200
    async with env.databases.transaction(LogicalDb.CRM) as s:
        ends = set(
            (
                await s.execute(
                    select(svc.AuthSession.expires_at).where(
                        svc.AuthSession.user_id == user_id, svc.AuthSession.revoked_at.is_(None)
                    )
                )
            ).scalars()
        )
    assert ends == {first_end}  # rotating does not extend the session: it ends when the sign-in does
    async with env.databases.transaction(LogicalDb.CRM) as s:
        cred = await s.get(StaffCredential, user_id)
        assert cred is not None
        cred.locked_until = env.clock() + timedelta(minutes=15)
    assert (await env.client.post("/v1/auth/refresh")).status_code == 401
    assert tokens["access_token"]


async def test_l6_reset_start_answers_before_the_code_is_mailed(env: Env) -> None:
    """L6: the reset code goes out as a background task, so a real address answers like an unknown one (the
    response no longer waits for the mail)."""
    _, _, email = await _ready_session(env)
    for address in (email, "nobody-at-all@test.ae"):
        r = await env.client.post("/v1/auth/password/reset/start", json={"email": address})
        assert r.status_code == 202
    assert env.mailer.sent[-1][0] == email  # the real address did get its code (after the response)
