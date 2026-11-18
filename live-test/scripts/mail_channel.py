"""Real email through the platform's own channel classes (kx.automation.channels.stalwart):
  send   one quote-style email with a PDF attachment, From Hamthan@kairoxaitech.com -> TO (Stalwart -> Hostinger)
  poll   the platform's MailboxPoller over the Stalwart inbox (prints From/Subject of each new message)
Run inside the app image on the kxlive network (settings from KX_AUTO_* env). Secrets never printed.
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import json
import sys
import time

import httpx

from kx.automation.channels.email import Attachment
from kx.automation.channels.stalwart import JmapClient, MailboxPoller, StalwartSender
from kx.automation.settings import SesSettings, StalwartSettings


def settings() -> tuple[StalwartSettings, SesSettings]:
    import os
    from pydantic import SecretStr

    st = StalwartSettings(url=os.environ["KX_AUTO_STALWART__URL"], username=os.environ["KX_AUTO_STALWART__USERNAME"],
                          password=SecretStr(os.environ["KX_AUTO_STALWART__PASSWORD"]))
    return st, SesSettings(from_address=os.environ["KX_AUTO_SES__FROM_ADDRESS"])


async def send(to: str) -> None:
    st, ses = settings()
    pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
    async with httpx.AsyncClient(timeout=st.timeout_seconds) as http:
        sender = StalwartSender(JmapClient(http, st), ses)
        t0 = time.perf_counter()
        mid = await sender.send(
            to_address=to,
            subject=f"[Kairox live test] Quotation Q-TEST-{time.strftime('%H%M%S')} - Arifa Packing",
            body=("Dear buyer,\n\nThis is a LIVE TEST of the Kairox platform email channel (Stalwart -> Hostinger).\n"
                  "Attached is a test quotation PDF.\n\nPlease REPLY to this email with any short text, "
                  "so the reply path (Gmail -> Hostinger -> Stalwart -> platform poll) is tested too.\n\n"
                  "Arifa Packing & Packaging LLC (test)"),
            attachment=Attachment("Q-TEST.pdf", "application/pdf", pdf),
            from_name="Arifa Pack (Kairox live test)",
        )
        print(json.dumps({"sent": True, "message_id": mid, "seconds": round(time.perf_counter() - t0, 3)}))


async def poll() -> None:
    st, _ = settings()
    seen: list[dict[str, str]] = []

    async def handle(email_id: str, raw: bytes) -> None:
        m = email.message_from_bytes(raw, policy=email.policy.default)
        seen.append({"id": email_id, "from": str(m["From"]), "subject": str(m["Subject"]),
                     "in_reply_to": str(m.get("In-Reply-To", "")), "bytes": str(len(raw))})

    async with httpx.AsyncClient(timeout=st.timeout_seconds) as http:
        n = await MailboxPoller(JmapClient(http, st), st.poll_batch).poll(handle)
    print(json.dumps({"handled": n, "messages": seen}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(send(sys.argv[2]) if sys.argv[1] == "send" else poll())
