"""M8 live check (19 Nov): is real Gmail mail still trusted, and is a forged sender not?

1. Real: the newest message from the Gmail buyer in the Hostinger inbox (read-only, BODY.PEEK) is delivered to
   Stalwart over SMTP, exactly as the MX hop / imap_bridge does, but to a staff TEST mailbox (`kxadmin`) so the
   platform does not process it again. It is read back over JMAP as Stalwart stored it (with the
   Authentication-Results header Stalwart adds) and passed to the platform's own `sender_verified`.
2. Forged: a message that claims the same Gmail From address, sent to Stalwart from this PC (no DKIM, wrong SPF).
Prints headers and verdicts only (no message bodies). Output: results/m8_check.json
"""

from __future__ import annotations

import asyncio
import imaplib
import json
import os
import smtplib
import sys
import time
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, os.path.dirname(__file__))
import envload  # noqa: E402
from kxclient import JMAP, SP  # noqa: E402

from kx.automation.email.channel import sender_verified  # noqa: E402

envload.load()
BUYER = "muhammadhamthan660@gmail.com"
BOX = "kxadmin"
RCPT = f"{BOX}@kairoxaitech.com"
STALWART = ("localhost", int(os.environ.get("STALWART_SMTP_PORT", "32525")))
OUT = Path(__file__).resolve().parents[1] / "results" / "m8_check.json"


def newest_real() -> bytes:
    m = imaplib.IMAP4_SSL("imap.hostinger.com", 993, timeout=30)
    m.login(os.environ["KX_MAIL_USER"], os.environ["KX_MAIL_PASSWORD"])
    m.select("INBOX", readonly=True)
    _, data = m.uid("search", None, f'(FROM "{BUYER}")')
    uid = data[0].split()[-1]
    _, msg = m.uid("fetch", uid, "(BODY.PEEK[])")
    m.logout()
    return msg[0][1]


def forged() -> bytes:
    e = EmailMessage()
    e["From"] = f"Buyer <{BUYER}>"
    e["To"] = RCPT
    e["Subject"] = "M8 forged-sender test"
    e["Message-ID"] = f"<m8-forged-{int(time.time())}@example.invalid>"
    e.set_content("Forged sender test (live-test harness).")
    return e.as_bytes()


async def stored(marker: str, after: float) -> bytes:
    pw = (SP / "staff_mailbox_pw.txt").read_text().strip()
    async with httpx.AsyncClient(base_url=JMAP, auth=(BOX, pw), timeout=20, follow_redirects=True) as c:
        session = (await c.get("/.well-known/jmap")).json()
        acct = session["primaryAccounts"]["urn:ietf:params:jmap:mail"]
        for _ in range(60):
            r = await c.post("/jmap/", json={"using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
                "methodCalls": [["Email/query", {"accountId": acct, "sort": [{"property": "receivedAt", "isAscending": False}],
                                                 "limit": 10}, "q"],
                                ["Email/get", {"accountId": acct, "#ids": {"resultOf": "q", "name": "Email/query", "path": "/ids"},
                                               "properties": ["receivedAt", "messageId", "blobId"]}, "g"]]})
            for e in r.json()["methodResponses"][1][1]["list"]:
                if marker in (e.get("messageId") or [""])[0]:
                    url = session["downloadUrl"].replace("{accountId}", acct).replace("{blobId}", e["blobId"])
                    url = url.replace("{name}", "m.eml").replace("{type}", "message%2Frfc822")
                    parts = urlsplit(url)  # Stalwart names its public host; keep the path, use the local port
                    return (await c.get(parts.path + (f"?{parts.query}" if parts.query else ""))).content
            await asyncio.sleep(1)
    raise RuntimeError(f"message {marker} not found in {BOX}'s mailbox")


def verdict(raw: bytes) -> dict[str, object]:
    msg = message_from_bytes(raw, policy=policy.default)
    ars = [" ".join(str(h).split())[:300] for h in msg.get_all("Authentication-Results", [])]
    return {"verified": sender_verified(msg), "authentication_results_top": ars[:1], "headers_count": len(ars)}


async def main() -> None:
    results = {}
    for name, raw in (("real_gmail", newest_real()), ("forged", forged())):
        mid = (message_from_bytes(raw, policy=policy.default).get("Message-ID") or "").strip("<> ")
        t0 = time.time()
        with smtplib.SMTP(*STALWART, timeout=30) as s:
            s.sendmail(BUYER, [RCPT], raw)
        results[name] = verdict(await stored(mid.split("@")[0], t0))
        print(name, json.dumps(results[name]), flush=True)
    results["pass"] = results["real_gmail"]["verified"] is True and results["forged"]["verified"] is False
    OUT.write_text(json.dumps(results, indent=1))
    print("M8 live check:", "PASS" if results["pass"] else "FAIL")


asyncio.run(main())
