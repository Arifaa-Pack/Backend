"""L1 + L7 (security review 19 Nov): a staff access token is honoured only while its sign-in session lives.

Before, a token was trusted for its whole 15 minutes: signing out, a password change, an MFA reset, a
deactivation or a role change did not stop it, and a user still on the one-time password could use every
route. Now every staff route first checks (cached for a few seconds, so about one small query per user every
CACHE_SECONDS) that:
* the session the token was issued for (claim `sid`), followed through its refresh rotations to the newest
  one, is neither revoked nor expired;
* the user is active and still has the role the token states;
* the one-time password has been changed: until then only `/v1/auth/*` works (change password, MFA, logout).
Tokens without `sid` (service tokens; tests that inject a principal) are left to the route's own checks.
"""

# No `from __future__ import annotations` here: FastAPI evaluates the closure's dependency annotations.

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Final

from fastapi import Depends, Request
from sqlalchemy import text

from kx.core.auth import JwtVerifier, bearer_principal, token_verifier
from kx.core.db import Databases
from kx.core.errors import Forbidden, Unauthorized
from kx.core.settings import LogicalDb

CACHE_SECONDS: Final = 10.0
AUTH_PREFIX: Final = "/v1/auth/"
LOGOUT_PATH: Final = AUTH_PREFIX + "logout"  # checked without the cache, and the cached state is dropped
ENDED: Final = "your session has ended; sign in again"
CHANGE_FIRST: Final = "change your one-time password first"

_STATE_SQL = text(
    """
    WITH RECURSIVE chain AS (
        SELECT id, replaced_by, revoked_at, expires_at, 0 AS depth
        FROM crm.auth_sessions WHERE id = :sid AND user_id = :uid
        UNION ALL
        SELECT s.id, s.replaced_by, s.revoked_at, s.expires_at, c.depth + 1
        FROM crm.auth_sessions s JOIN chain c ON s.id = c.replaced_by
        WHERE c.depth < 500
    )
    SELECT c.revoked_at IS NULL AND c.expires_at > :now AS alive, u.is_active, u.role,
           coalesce(cr.must_change_password, false) AS must_change
    FROM chain c
    JOIN crm.users u ON u.id = :uid
    LEFT JOIN crm.staff_credentials cr ON cr.user_id = :uid
    WHERE c.replaced_by IS NULL
    ORDER BY c.depth DESC
    LIMIT 1
    """
)


@dataclass(frozen=True, slots=True)
class _State:
    alive: bool
    role: str | None
    must_change: bool


class SessionGuard:
    def __init__(self, databases: Databases, now: Callable[[], datetime], cache_seconds: float | None = None):
        self._db = databases
        self._now = now
        self._ttl = CACHE_SECONDS if cache_seconds is None else cache_seconds  # read now: tests set it to 0
        self._cache: dict[uuid.UUID, tuple[float, _State]] = {}

    async def state(self, session_id: uuid.UUID, user_id: uuid.UUID, *, cached: bool = True) -> _State:
        hit = self._cache.get(session_id) if cached else None
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
        async with self._db.transaction(LogicalDb.CRM) as s:
            row = (
                await s.execute(_STATE_SQL, {"sid": session_id, "uid": user_id, "now": self._now()})
            ).first()
        state = (
            _State(False, None, False)
            if row is None
            else _State(bool(row.alive and row.is_active), str(row.role), bool(row.must_change))
        )
        if not cached:  # signing out: the next request of this session reads the database again
            self._cache.pop(session_id, None)
            return state
        if len(self._cache) > 10_000:  # bounded: a burst of sign-ins never grows it without limit
            self._cache.clear()
        self._cache[session_id] = (time.monotonic() + self._ttl, state)
        return state

    def dependency(self) -> Callable[..., Awaitable[None]]:
        async def live_session(
            request: Request, verifier: Annotated[JwtVerifier, Depends(token_verifier)]
        ) -> None:
            p = bearer_principal(request.headers.get("authorization"), verifier)
            if p is None or p.session_id is None or p.user_id is None:
                return
            state = await self.state(p.session_id, p.user_id, cached=request.url.path != LOGOUT_PATH)
            if not state.alive or state.role != p.role.value:
                raise Unauthorized(ENDED)
            if state.must_change and not request.url.path.startswith(AUTH_PREFIX):
                raise Forbidden(CHANGE_FIRST)

        return live_session
