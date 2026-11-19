"""In-house staff login + MFA (11 Nov, D26; replaces Amazon Cognito).

Flow (platform docs: Recommendation "Staff login + MFA", Fallback plan "Staff login"):
1. `login(email, password)` -> a short-lived challenge and the second steps on offer.
2. `verify_second_step(challenge, code, "totp")` -> tokens with `mfa=true` (authenticator app, the main
   factor), or `"email"` after `send_email_code` -> tokens with `mfa=false` (fallback: the person is in,
   but approvals, price-list edits and settings wait for the TOTP factor; NIST SP 800-63B-4, OPEN #49).
3. Access token: RS256, 15 minutes. Refresh token: random, only its hash stored, rotated on every use.
* 5 wrong passwords or codes from one client IP lock that IP out of the account for 15 minutes; 50 from all
  IPs lock the account (M3: one attacker cannot keep a staff member out from everywhere). Sign-in, the email
  code and password recovery have per-IP and per-account rate limits (`kx.crm.throttle`). Every login,
  failure, lock and MFA change is audited.
* A break-glass admin still needs its TOTP (no MFA bypass) and every use notifies the MD and the manager.

Functions return an outcome instead of raising for a refused attempt, so the caller can commit the
failed-attempt counter before answering 401/429 (a raised error would roll it back).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kx.core import events as ev
from kx.core.errors import Forbidden, NotFound, RuleViolation, Unavailable
from kx.core.event_payloads import StaffNotice
from kx.core.identity import new_id, normalise_email
from kx.core.logging import get_logger
from kx.core.passwords import (
    hash_password,
    new_numeric_code,
    new_token,
    new_totp_secret,
    otpauth_uri,
    password_problem,
    temporary_password,
    token_hash,
    verify_password,
    verify_totp,
)
from kx.core.settings import RateLimit
from kx.crm._shared import staff
from kx.crm._shared.journal import Actor, Emit, record
from kx.crm.auth.throttle import account_key, ip_key
from kx.crm.deps import CrmDeps
from kx.crm.models import AuthChallenge, AuthSession, StaffCredential, User

log = get_logger(__name__)
Factor = Literal["totp", "email"]
GENERIC: Final = "email or password is not correct"
BUSY: Final = "too many requests; try again later"  # M3 rate limit (never says whether the account exists)
LOCKED: Final = "too many failed attempts; try again later"
_DUMMY_HASH: Final = hash_password("dummy-password-for-timing")  # same work whether or not the user exists


@dataclass(frozen=True, slots=True)
class Refused:
    status: Literal[401, 403, 429]
    detail: str


@dataclass(frozen=True, slots=True)
class Challenge:
    token: str
    methods: tuple[Factor, ...]
    totp_enrolled: bool
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Tokens:
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime
    mfa: bool
    amr: tuple[str, ...]
    user: User
    must_change_password: bool
    totp_enrolled: bool


def _system() -> Actor:
    return Actor.system("staff_login")


def _as_user(user: User) -> Actor:
    return Actor("user", str(user.id), user.id)


async def _user_by_email(session: AsyncSession, email: str) -> User | None:
    try:
        address = normalise_email(email)
    except ValueError:
        return None
    return (await session.execute(select(User).where(User.email == address))).scalar_one_or_none()


async def _over_limit(deps: CrmDeps, *buckets: tuple[str, str, RateLimit]) -> bool:
    """M3: True when any (scope, key, limit) bucket is empty (each one is charged until one refuses)."""
    for scope, key, limit in buckets:
        if not await deps.throttle.take(scope, key, limit):
            log.warning("auth.rate_limited", scope=scope)
            return True
    return False


def _fail_key(email: str, ip: str | None) -> str:
    return f"fail:{account_key(email)}:{ip_key(ip)}"


async def _locked(
    deps: CrmDeps, cred: StaffCredential | None, email: str, ip: str | None, now: datetime
) -> bool:
    """M3: locked for this client IP (per account + IP failures) or, as the backstop, for every IP."""
    if cred is not None and cred.locked_until is not None and cred.locked_until > now:
        return True
    return await deps.throttle.count(_fail_key(email, ip)) >= deps.auth.max_failed_attempts


# ---------------------------------------------------------------- step 1: password
async def login(
    session: AsyncSession, deps: CrmDeps, email: str, password: str, *, ip: str | None = None
) -> Challenge | Refused:
    now = deps.now()
    a = deps.auth
    if await _over_limit(
        deps,
        ("login_ip", ip_key(ip), a.rate_login_ip),
        ("login_account", account_key(email), a.rate_login_account),
    ):
        return Refused(429, BUSY)
    user = await _user_by_email(session, email)
    cred = await session.get(StaffCredential, user.id, with_for_update=True) if user else None
    who = str(user.email) if user else email
    if await _locked(deps, cred, who, ip, now):  # an unknown address locks the same way (no enumeration)
        return Refused(429, LOCKED)
    if user is None or cred is None or not user.is_active:
        verify_password(password, _DUMMY_HASH)
        await deps.throttle.bump(_fail_key(who, ip), a.lockout_minutes * 60)
        return Refused(401, GENERIC)
    if not verify_password(password, cred.password_hash):
        await _failed(session, deps, user, cred, now=now, what="password", ip=ip)
        return Refused(401, GENERIC)
    token = new_token()
    expires = now + timedelta(seconds=deps.auth.code_ttl_seconds)
    session.add(
        AuthChallenge(
            id=new_id(), user_id=user.id, purpose="login", token_hash=token_hash(token), expires_at=expires
        )
    )
    await session.flush()
    enrolled = cred.totp_enabled_at is not None
    return Challenge(token, ("totp", "email") if enrolled else ("email",), enrolled, expires)


async def _failed(
    session: AsyncSession,
    deps: CrmDeps,
    user: User,
    cred: StaffCredential,
    *,
    now: datetime,
    what: str,
    ip: str | None,
) -> None:
    """M3: a wrong password or code counts for this account + client IP (`max_failed_attempts` locks that IP
    out for `lockout_minutes`) and toward the account-wide backstop (`account_lock_failures`)."""
    lock_seconds = deps.auth.lockout_minutes * 60
    from_ip = await deps.throttle.bump(_fail_key(str(user.email), ip), lock_seconds)
    cred.failed_attempts += 1
    account_locked = cred.failed_attempts >= deps.auth.account_lock_failures
    if account_locked:
        cred.locked_until = now + timedelta(minutes=deps.auth.lockout_minutes)
        cred.failed_attempts = 0
    ip_locked = from_ip >= deps.auth.max_failed_attempts
    await session.flush()
    await record(
        session,
        _system(),
        "auth.locked" if account_locked or ip_locked else "auth.login_failed",
        "user",
        user.id,
        after={
            "factor": what,
            "ip": ip,
            "scope": "account" if account_locked else "ip" if ip_locked else None,
            "locked_until": (
                cred.locked_until
                if account_locked
                else now + timedelta(seconds=lock_seconds)
                if ip_locked
                else None
            ),
        },
    )


# ---------------------------------------------------------------- step 2: TOTP or email code
async def _open_challenge(
    session: AsyncSession, token: str, now: datetime, purpose: str = "login"
) -> AuthChallenge | None:
    row = (
        await session.execute(
            select(AuthChallenge)
            .where(AuthChallenge.token_hash == token_hash(token), AuthChallenge.purpose == purpose)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.consumed_at is not None or row.expires_at <= now:
        return None
    return row


async def send_email_code(
    session: AsyncSession, deps: CrmDeps, token: str, *, ip: str | None = None
) -> tuple[str, str] | Refused:
    """Fallback second step: a 6-digit code to the work address. Returns (address, code) for the caller to
    send after commit; only the code's hash is stored. M3: limited per client IP and per account."""
    now = deps.now()
    if await _over_limit(deps, ("mfa_send_ip", ip_key(ip), deps.auth.rate_mfa_send_ip)):
        return Refused(429, BUSY)
    challenge = await _open_challenge(session, token, now)
    if challenge is None:
        return Refused(401, "the sign-in step expired; start again")
    user = await session.get(User, challenge.user_id)
    assert user is not None  # FK
    if await _over_limit(deps, ("mfa_send_account", str(user.id), deps.auth.rate_mfa_send_account)):
        return Refused(429, BUSY)
    code = new_numeric_code()
    challenge.code_hash, challenge.tries = token_hash(code), 0
    challenge.expires_at = now + timedelta(seconds=deps.auth.code_ttl_seconds)
    await session.flush()
    return str(user.email), code


async def verify_second_step(
    session: AsyncSession,
    deps: CrmDeps,
    token: str,
    code: str,
    factor: Factor,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> Tokens | Refused:
    now = deps.now()
    challenge = await _open_challenge(session, token, now)
    if challenge is None:
        return Refused(401, "the sign-in step expired; start again")
    user = await session.get(User, challenge.user_id)
    cred = await session.get(StaffCredential, challenge.user_id, with_for_update=True)
    assert user is not None and cred is not None  # FK
    if await _locked(deps, cred, str(user.email), ip, now):  # wrong codes count toward the lock too
        return Refused(429, LOCKED)
    challenge.tries += 1
    if challenge.tries > deps.auth.code_max_tries:
        challenge.consumed_at = now
        await session.flush()
        return Refused(429, "too many codes tried; sign in again")
    ok = _check_factor(deps, cred, code=code, factor=factor, challenge=challenge, now=now)
    if not ok:
        await _failed(session, deps, user, cred, now=now, what=factor, ip=ip)
        return Refused(401, "the code is not correct")
    challenge.consumed_at = now
    amr = ("pwd", "otp") if factor == "totp" else ("pwd", "email")
    await deps.throttle.clear(_fail_key(str(user.email), ip))  # signed in from here: this IP starts afresh
    return await _issue(session, deps, user, cred, amr, ip=ip, user_agent=user_agent)


def _check_factor(
    deps: CrmDeps,
    cred: StaffCredential,
    *,
    code: str,
    factor: Factor,
    challenge: AuthChallenge,
    now: datetime,
) -> bool:
    if factor == "email":
        return challenge.code_hash is not None and challenge.code_hash == token_hash(code.strip())
    if cred.totp_secret_enc is None or cred.totp_enabled_at is None or deps.seed_box is None:
        return False
    step = verify_totp(deps.seed_box.open(cred.totp_secret_enc), code, now, last_step=cred.totp_last_step)
    if step is None:
        return False
    cred.totp_last_step = step  # a code can never be replayed
    return True


async def _issue(
    session: AsyncSession,
    deps: CrmDeps,
    user: User,
    cred: StaffCredential,
    amr: tuple[str, ...],
    *,
    ip: str | None,
    user_agent: str | None,
    expires_at: datetime | None = None,
) -> Tokens:
    if deps.tokens is None:
        raise Unavailable("staff login is not configured (no token signing key)")
    now = deps.now()
    mfa = "otp" in amr
    refresh = new_token()
    # L7: a refreshed session keeps the sign-in's end (absolute lifetime = refresh_ttl_seconds)
    expires = expires_at or now + timedelta(seconds=deps.auth.refresh_ttl_seconds)
    session_id = new_id()
    session.add(
        AuthSession(
            id=session_id,
            user_id=user.id,
            token_hash=token_hash(refresh),
            amr=list(amr),
            mfa=mfa,
            expires_at=expires,
            user_agent=(user_agent or "")[:300] or None,
            ip=ip,
        )
    )
    cred.last_login_at, cred.failed_attempts, cred.locked_until = now, 0, None
    await session.flush()
    emits = (await _break_glass_notice(session, user, now),) if cred.is_break_glass else ()
    await record(session, _as_user(user), "auth.login", "user", user.id, after={"amr": list(amr)}, emit=emits)
    return Tokens(
        access_token=deps.tokens.issue(
            user_id=user.id, role=user.role, mfa=mfa, amr=amr, now=now, session_id=session_id
        ),
        expires_in=deps.tokens.ttl_seconds,
        refresh_token=refresh,
        refresh_expires_at=expires,
        mfa=mfa,
        amr=amr,
        user=user,
        must_change_password=cred.must_change_password,
        totp_enrolled=cred.totp_enabled_at is not None,
    )


async def _break_glass_notice(session: AsyncSession, user: User, now: datetime) -> Emit:
    """Fallback plan: "Break-glass admin account (no MFA bypass, every use alerted)"."""
    people = {
        u.id: staff.staff_contact(u)
        for u in (await staff.md_user(session), await staff.manager_user(session))
    }
    people.pop(user.id, None)
    return Emit(
        ev.STAFF_NOTICE,
        "user",
        user.id,
        StaffNotice(
            deal_ref=None,
            notify=tuple(people.values()),
            text=f"Break-glass admin account {user.email} signed in at {now:%Y-%m-%d %H:%M} UTC.",
            key=f"break-glass:{user.id}:{now:%Y%m%d%H%M%S}",
        ),
    )


# ---------------------------------------------------------------- refresh / logout
async def refresh(
    session: AsyncSession, deps: CrmDeps, token: str, *, ip: str | None = None, user_agent: str | None = None
) -> Tokens | Refused:
    """Rotate: the presented refresh token is revoked and a new session replaces it. A reused (already
    rotated) token revokes every session of that user (token theft)."""
    now = deps.now()
    row = (
        await session.execute(
            select(AuthSession).where(AuthSession.token_hash == token_hash(token)).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        return Refused(401, "session expired; sign in again")
    if row.revoked_at is not None:
        if row.replaced_by is not None:  # replay of a rotated token
            await _revoke_all(session, row.user_id, now)
        return Refused(401, "session expired; sign in again")
    user = await session.get(User, row.user_id)
    cred = await session.get(StaffCredential, row.user_id)
    if row.expires_at <= now or user is None or cred is None or not user.is_active:
        return Refused(401, "session expired; sign in again")
    if cred.locked_until is not None and cred.locked_until > now:  # L7: a locked account cannot refresh
        return Refused(401, "session expired; sign in again")
    tokens = await _issue(
        session, deps, user, cred, tuple(row.amr), ip=ip, user_agent=user_agent, expires_at=row.expires_at
    )
    new_session = await session.scalar(
        select(AuthSession.id).where(AuthSession.token_hash == token_hash(tokens.refresh_token))
    )
    row.revoked_at, row.replaced_by = now, new_session
    await session.flush()
    return tokens


async def _revoke_all(
    session: AsyncSession, user_id: uuid.UUID, now: datetime, *, keep: uuid.UUID | None = None
) -> None:
    stmt = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if keep is not None:
        stmt = stmt.where(AuthSession.id != keep)
    await session.execute(stmt.values(revoked_at=now))


async def logout(session: AsyncSession, deps: CrmDeps, token: str) -> None:
    await session.execute(
        update(AuthSession)
        .where(AuthSession.token_hash == token_hash(token), AuthSession.revoked_at.is_(None))
        .values(revoked_at=deps.now())
    )


# ---------------------------------------------------------------- TOTP enrolment, passwords
async def begin_totp(
    session: AsyncSession, deps: CrmDeps, user_id: uuid.UUID, *, mfa: bool
) -> tuple[str, str, str | None]:
    """A new seed (not active until confirmed with a code). Returns (secret, otpauth URI, address to warn).

    D4 (architecture guide): replacing an ACTIVE authenticator needs a TOTP (MFA) session. Otherwise anyone
    who can read the staff member's mailbox could sign in with an email code, swap the authenticator and gain
    approval rights. A lost phone is reset by an admin (`reset_mfa`), after which enrolling works again from
    an email-code session. A replacement is journalled and the user is told by email (third value)."""
    if deps.seed_box is None:
        raise Unavailable("TOTP is not configured (no KX_AUTH__ENCRYPTION_KEY)")
    user, cred = await _user_and_cred(session, user_id)
    replacing = cred.totp_enabled_at is not None
    if replacing and not mfa:
        raise Forbidden(
            "sign in with your authenticator app to replace it; if you lost it, ask an admin to reset it"
        )
    secret = new_totp_secret()
    cred.totp_secret_enc, cred.totp_enabled_at, cred.totp_last_step = deps.seed_box.seal(secret), None, None
    await session.flush()
    if replacing:
        await record(session, _as_user(user), "auth.totp_replacement_started", "user", user.id)
    return (
        secret,
        otpauth_uri(secret, str(user.email), deps.auth.totp_issuer),
        str(user.email) if replacing else None,
    )


async def confirm_totp(session: AsyncSession, deps: CrmDeps, user_id: uuid.UUID, code: str) -> bool:
    user, cred = await _user_and_cred(session, user_id)
    if cred.totp_secret_enc is None or deps.seed_box is None:
        return False
    step = verify_totp(deps.seed_box.open(cred.totp_secret_enc), code, deps.now())
    if step is None:
        return False
    cred.totp_enabled_at, cred.totp_last_step = deps.now(), step
    await session.flush()
    await record(session, _as_user(user), "auth.totp_enrolled", "user", user.id)
    return True


async def change_password(
    session: AsyncSession,
    deps: CrmDeps,
    user_id: uuid.UUID,
    current: str,
    new: str,
    *,
    ip: str | None = None,
    keep_session: uuid.UUID | None = None,
) -> Refused | None:
    """The signed-in user changes their password: every OTHER session ends, the one in use stays (L1: the
    API now checks sessions on every request, so ending it too would sign the user out mid-way)."""
    user, cred = await _user_and_cred(session, user_id)
    if not verify_password(current, cred.password_hash):
        await _failed(session, deps, user, cred, now=deps.now(), what="password_change", ip=ip)
        return Refused(401, "the current password is not correct")
    keep = await _chain_head(session, keep_session) if keep_session is not None else None
    await _set_password(session, deps, user, cred, new, must_change=False, keep=keep)
    return None


async def _chain_head(session: AsyncSession, session_id: uuid.UUID) -> uuid.UUID:
    """The newest session of a refresh-rotation chain (a token may name an older link of it)."""
    current = session_id
    for _ in range(500):
        nxt = await session.scalar(select(AuthSession.replaced_by).where(AuthSession.id == current))
        if nxt is None:
            return current
        current = nxt
    return current


async def _set_password(
    session: AsyncSession,
    deps: CrmDeps,
    user: User,
    cred: StaffCredential,
    new: str,
    *,
    must_change: bool,
    keep: uuid.UUID | None = None,
) -> None:
    if (problem := password_problem(new)) is not None:
        raise RuleViolation(problem, reason="weak_password")
    cred.password_hash, cred.password_changed_at, cred.must_change_password = (
        hash_password(new),
        deps.now(),
        must_change,
    )
    await _revoke_all(session, user.id, deps.now(), keep=keep)  # the other sessions end with the old password
    await session.flush()
    await record(session, _system(), "auth.password_changed", "user", user.id)


def _reset_wrong_key(email: str) -> str:
    return f"reset_wrong:{account_key(email)}"


async def _reset_refused(deps: CrmDeps, email: str, ip: str | None, step: Literal["start", "finish"]) -> bool:
    """M3: per-IP and per-account buckets, and the reset lock after `reset_max_wrong_codes` wrong codes.
    Keyed by the typed address, so an unknown address is refused exactly like a real one."""
    a = deps.auth
    ip_limit, account_limit = (
        (a.rate_reset_start_ip, a.rate_reset_start_account)
        if step == "start"
        else (a.rate_reset_finish_ip, a.rate_reset_finish_account)
    )
    if await _over_limit(
        deps,
        (f"reset_{step}_ip", ip_key(ip), ip_limit),
        (f"reset_{step}_account", account_key(email), account_limit),
    ):
        return True
    return await deps.throttle.count(_reset_wrong_key(email)) >= a.reset_max_wrong_codes


async def start_reset(
    session: AsyncSession, deps: CrmDeps, email: str, *, ip: str | None = None
) -> tuple[str, str] | Refused | None:
    """Recovery by email code. Returns (address, code) to send, or None (unknown address: same answer to the
    caller, so nobody learns which addresses exist). M3: rate limited (at most `rate_reset_start_account`
    starts per account), and only the newest code is live: a new start ends the previous one."""
    if await _reset_refused(deps, email, ip, "start"):
        return Refused(429, BUSY)
    user = await _user_by_email(session, email)
    if user is None or not user.is_active or await session.get(StaffCredential, user.id) is None:
        return None
    await session.execute(
        update(AuthChallenge)
        .where(
            AuthChallenge.user_id == user.id,
            AuthChallenge.purpose == "reset",
            AuthChallenge.consumed_at.is_(None),
        )
        .values(consumed_at=deps.now())
    )
    code = new_numeric_code()
    session.add(
        AuthChallenge(
            id=new_id(),
            user_id=user.id,
            purpose="reset",
            token_hash=token_hash(f"reset:{user.id}:{new_token()}"),
            code_hash=token_hash(code),
            expires_at=deps.now() + timedelta(seconds=deps.auth.code_ttl_seconds),
        )
    )
    await session.flush()
    return str(user.email), code


async def finish_reset(
    session: AsyncSession, deps: CrmDeps, email: str, code: str, new: str, *, ip: str | None = None
) -> Refused | None:
    """M3: every wrong code (or unknown address) counts toward the reset lock of that address."""
    now = deps.now()
    if await _reset_refused(deps, email, ip, "finish"):
        return Refused(429, BUSY)
    wrong = _reset_wrong_key(email)
    user = await _user_by_email(session, email)
    if user is None:
        await deps.throttle.bump(wrong, deps.auth.lockout_minutes * 60)
        return Refused(401, "the code is not correct or has expired")
    row = (
        await session.execute(
            select(AuthChallenge)
            .where(
                AuthChallenge.user_id == user.id,
                AuthChallenge.purpose == "reset",
                AuthChallenge.consumed_at.is_(None),
                AuthChallenge.expires_at > now,
            )
            .order_by(AuthChallenge.created_at.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        await deps.throttle.bump(wrong, deps.auth.lockout_minutes * 60)
        return Refused(401, "the code is not correct or has expired")
    row.tries += 1
    if row.tries > deps.auth.code_max_tries or row.code_hash != token_hash(code.strip()):
        if row.tries > deps.auth.code_max_tries:
            row.consumed_at = now
        await session.flush()
        await deps.throttle.bump(wrong, deps.auth.lockout_minutes * 60)
        return Refused(401, "the code is not correct or has expired")
    row.consumed_at = now
    _, cred = await _user_and_cred(session, user.id)
    await _set_password(session, deps, user, cred, new, must_change=False)
    await deps.throttle.clear(wrong)
    return None


# ---------------------------------------------------------------- administration
async def create_user(
    session: AsyncSession,
    deps: CrmDeps,
    *,
    email: str,
    full_name: str,
    role: str,
    actor: Actor,
    phone_e164: str | None = None,
    break_glass: bool = False,
) -> tuple[User, str]:
    """A staff user with a one-time password (must be changed at first login; then the person enrols an
    authenticator app). Returns (user, temporary password) — shown once, never stored."""
    address = normalise_email(email)
    if await _user_by_email(session, address) is not None:
        raise RuleViolation("a user with this email exists", reason="duplicate")
    user = User(id=new_id(), email=address, full_name=full_name, role=role, phone_e164=phone_e164)
    session.add(user)
    await session.flush()
    password = temporary_password()
    session.add(
        StaffCredential(user_id=user.id, password_hash=hash_password(password), is_break_glass=break_glass)
    )
    await session.flush()
    await record(
        session, actor, "user.created", "user", user.id, after={"role": role, "break_glass": break_glass}
    )
    return user, password


async def reset_mfa(session: AsyncSession, deps: CrmDeps, user_id: uuid.UUID, actor: Actor) -> None:
    """A lost authenticator: the person signs in with the email code and enrols a new one."""
    user, cred = await _user_and_cred(session, user_id)
    cred.totp_secret_enc = cred.totp_enabled_at = cred.totp_last_step = None
    await _revoke_all(session, user.id, deps.now())
    await session.flush()
    await record(session, actor, "auth.mfa_reset", "user", user.id)


async def unlock(session: AsyncSession, deps: CrmDeps, user_id: uuid.UUID, actor: Actor) -> None:
    user, cred = await _user_and_cred(session, user_id)
    cred.locked_until, cred.failed_attempts = None, 0
    await session.flush()
    await record(session, actor, "auth.unlocked", "user", user.id)


async def _user_and_cred(session: AsyncSession, user_id: uuid.UUID) -> tuple[User, StaffCredential]:
    user = await session.get(User, user_id)
    cred = await session.get(StaffCredential, user_id, with_for_update=True)
    if user is None or cred is None:
        raise NotFound("staff user not found")
    return user, cred


async def active_sessions(session: AsyncSession, user_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count()).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        )
        or 0
    )
