"""Who may edit website content (SRS §3, D54): the content editor, the admin and the manager (the MD passes
wherever MANAGER is listed, `kx.core.auth.Principal.has`)."""

from __future__ import annotations

from typing import Final

from kx.core.auth import Principal, Role

EDITORS: Final = (Role.CONTENT_EDITOR, Role.ADMIN, Role.MANAGER)


def actor_of(p: Principal) -> str:
    return f"user:{p.user_id}" if p.user_id else p.subject
