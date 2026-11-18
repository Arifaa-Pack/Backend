"""Shared live-test client: staff sign-in through the platform's real MFA. The e-mailed sign-in code is read from
the staff member's own Stalwart mailbox over JMAP (local domain, never leaves the box); first sign-in changes the
one-time password and enrols TOTP, like a real person. State (passwords, TOTP secrets) lives in the scratchpad."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import struct
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

API = os.environ.get("LIVE_API", "http://localhost:38000")
JMAP = os.environ.get("LIVE_JMAP", "http://localhost:38090")
SP = Path(os.environ.get("LIVE_SP", r"C:\Users\HEADUS~1\AppData\Local\Temp\claude\9921b7e85efa223a\6a537a94-221d-4813-ae9f-fdd35c083b5d\scratchpad"))
STATE = SP / "staff_state.json"
WA_SECRET = b"live-wa-secret"


def totp(secret_b32: str, at: float | None = None) -> str:
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    counter = int((at or time.time()) // 30)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def load_state() -> dict[str, dict[str, str]]:
    if STATE.exists():
        return json.loads(STATE.read_text())
    st: dict[str, dict[str, str]] = {}
    for line in (SP / "staff_users.txt").read_text().splitlines():
        u, role, pw = line.split()
        st[u] = {"role": role, "password": pw, "email": f"{u}@kairoxaitech.com"}
    STATE.write_text(json.dumps(st))
    return st


def save_state(st: dict[str, Any]) -> None:
    STATE.write_text(json.dumps(st))


async def latest_code(user: str, after: float) -> str:
    """The newest 6-digit code mailed to <user>@kairoxaitech.com after `after` (polls up to 60 s)."""
    pw = (SP / "staff_mailbox_pw.txt").read_text().strip()
    async with httpx.AsyncClient(base_url=JMAP, auth=(user, pw), timeout=15, follow_redirects=True) as c:
        acct = (await c.get("/.well-known/jmap")).json()["primaryAccounts"]["urn:ietf:params:jmap:mail"]
        for _ in range(60):
            r = await c.post("/jmap/", json={"using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
                "methodCalls": [["Email/query", {"accountId": acct, "sort": [{"property": "receivedAt", "isAscending": False}],
                                                 "limit": 5}, "q"],
                                ["Email/get", {"accountId": acct, "#ids": {"resultOf": "q", "name": "Email/query", "path": "/ids"},
                                               "properties": ["receivedAt", "subject", "preview"]}, "g"]]})
            for e in r.json()["methodResponses"][1][1]["list"]:
                ts = time.mktime(time.strptime(e["receivedAt"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
                m = re.search(r"\b(\d{6})\b", f"{e.get('subject', '')} {e.get('preview', '')}")
                if m and ts >= after - 2:
                    return m.group(1)
            await asyncio.sleep(1)
    raise RuntimeError(f"no sign-in code for {user}")


async def _wait_window() -> None:
    await asyncio.sleep(31 - (time.time() % 30))


async def login(c: httpx.AsyncClient, user: str, *, attempt: int = 0) -> str:
    """Access token for a staff user (handles first sign-in: email code, password change, TOTP enrolment)."""
    st = load_state()
    s = st[user]
    r = await c.post("/v1/auth/login", json={"email": s["email"], "password": s["password"]})
    r.raise_for_status()
    challenge = r.json()["challenge_token"]
    if s.get("totp_secret"):
        r = await c.post("/v1/auth/mfa/totp", json={"challenge_token": challenge, "code": totp(s["totp_secret"])})
        if r.status_code != 200 and attempt < 3:  # same 30 s code reused: wait for the next window
            await _wait_window()
            return await login(c, user, attempt=attempt + 1)
        r.raise_for_status()
        return str(r.json()["access_token"])
    t0 = time.time()
    (await c.post("/v1/auth/mfa/email/send", json={"challenge_token": challenge})).raise_for_status()
    code = await latest_code(user, t0)
    r = await c.post("/v1/auth/mfa/email/verify", json={"challenge_token": challenge, "code": code})
    r.raise_for_status()
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    if not s.get("changed"):
        new_pw = "Live-" + uuid.uuid4().hex
        r = await c.post("/v1/auth/password", headers=h, json={"current_password": s["password"], "new_password": new_pw})
        r.raise_for_status()
        s["password"], s["changed"] = new_pw, "1"
        save_state(st)
        return await login(c, user)
    r = await c.post("/v1/auth/totp/enrol", headers=h)
    r.raise_for_status()
    secret = r.json()["secret"]
    (await c.post("/v1/auth/totp/confirm", headers=h, json={"code": totp(secret)})).raise_for_status()
    s["totp_secret"] = secret
    save_state(st)
    await _wait_window()
    return await login(c, user)


def wa_payload(phone: str, name: str, text: str, msg_id: str | None = None) -> bytes:
    body = {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "971400000001", "phone_number_id": "100000000000001"},
        "contacts": [{"wa_id": phone, "profile": {"name": name}}],
        "messages": [{"from": phone, "id": msg_id or f"wamid.{uuid.uuid4().hex}", "timestamp": str(int(time.time())),
                      "type": "text", "text": {"body": text}}]}}]}]}
    return json.dumps(body, ensure_ascii=False).encode()


def wa_sig(raw: bytes, secret: bytes = WA_SECRET) -> str:
    return "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()
