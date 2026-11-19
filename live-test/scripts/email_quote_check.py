"""Round-2 re-test (19 Nov): ONE real quote e-mail to the Gmail buyer, checked as the buyer receives it.

The deal opened by the buyer's e-mail (D-2026-00001, salesperson kxsales2) gets a revised quote at a price above
the tier minimum; every approval is decided with the role it needs; then the e-mail the platform sent is read
back from the platform's own Stalwart mailbox (Sent copy) and checked:
  * the subject names the quotation ("Quotation Q-YYYY-NNNNN from ..."; a quote e-mail has its own subject, so
    it is not sent as "Re:" to the buyer's thread: that threading is for AI replies, which have none);
  * the greeting does not say "Hi Customer";
  * the quote PDF is attached and its "To" block names the buyer (text read with pdftotext).
Output: results/email_quote_check.json and results/retest_quote.pdf. Message bodies are not printed.
--check-only: no new quote; re-check the newest e-mail already sent to the buyer.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from email import message_from_bytes, policy
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, os.path.dirname(__file__))
import envload  # noqa: E402
import kxclient as k  # noqa: E402

envload.load()
BUYER = "muhammadhamthan660@gmail.com"
RES = Path(__file__).resolve().parents[1] / "results"
PRICE_USD = "0.0715"  # = AED 0.2626 per piece, above the 10,000-49,999 tier minimum of AED 0.255


def sql(db: str, q: str) -> str:
    return subprocess.run(["docker", "exec", "kxlive-postgres-1", "psql", "-U", "postgres", "-d", db, "-tAc", q],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60).stdout.strip()


async def sent_copy(after: float) -> bytes:
    """The newest message to the buyer in the platform mailbox, sent after `after`."""
    user, pw = os.environ.get("STALWART_USER", "hamthan"), os.environ["KX_MAIL_PASSWORD"]
    async with httpx.AsyncClient(base_url=k.JMAP, auth=(user, pw), timeout=30, follow_redirects=True) as c:
        session = (await c.get("/.well-known/jmap")).json()
        acct = session["primaryAccounts"]["urn:ietf:params:jmap:mail"]
        for _ in range(90):
            r = await c.post("/jmap/", json={"using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
                "methodCalls": [["Email/query", {"accountId": acct, "filter": {"to": BUYER},
                                                 "sort": [{"property": "receivedAt", "isAscending": False}], "limit": 3}, "q"],
                                ["Email/get", {"accountId": acct, "#ids": {"resultOf": "q", "name": "Email/query", "path": "/ids"},
                                               "properties": ["receivedAt", "blobId", "subject"]}, "g"]]})
            for e in r.json()["methodResponses"][1][1]["list"]:
                ts = time.mktime(time.strptime(e["receivedAt"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
                if ts >= after - 5:
                    url = session["downloadUrl"].replace("{accountId}", acct).replace("{blobId}", e["blobId"])
                    url = url.replace("{name}", "m.eml").replace("{type}", "message%2Frfc822")
                    parts = urlsplit(url)
                    return (await c.get(parts.path + (f"?{parts.query}" if parts.query else ""))).content
            await asyncio.sleep(2)
    raise RuntimeError("no sent copy of the quote e-mail found")


async def main() -> None:
    out: dict[str, object] = {}
    if "--check-only" in sys.argv:
        return await check(out, 0.0)
    deal_id = sql("pack_crm", "select d.id from crm.deals d join crm.contacts c on c.id = d.primary_contact_id "
                              f"where c.email = '{BUYER}' order by d.created_at limit 1")
    async with httpx.AsyncClient(base_url=k.API, timeout=60) as c:
        h = {u: {"Authorization": f"Bearer {await k.login(c, u)}"} for u in ("kxsales2", "kxmgr", "kxmd")}
        deal = (await c.get(f"/v1/deals/{deal_id}", headers=h["kxmgr"])).json()
        out["deal"] = {"number": deal["number"], "stage": deal["stage"], "currency": deal.get("currency")}
        t0 = time.time()
        body = {"currency": "USD", "lines": [{"line_no": 1, "unit_price": PRICE_USD}],
                "buyer_note": "Live re-test of the Kairox platform (19 Nov): revised quotation in USD.",
                "note": "Round-2 re-test: e-mail polish check (greeting, Re: subject, PDF To block)"}
        r = await c.post(f"/v1/deals/{deal_id}/quotes/revise", headers=h["kxsales2"], json=body)
        out["revise"] = r.status_code
        r.raise_for_status()
        version = r.json()
        decided = []
        for _ in range(6):
            appr = []
            for u in ("kxmd", "kxmgr", "kxsales2"):
                items = (await c.get("/v1/approvals", headers=h[u], params={"status": "open", "limit": 100})).json()
                appr += [a for a in items.get("items", []) if a.get("quote_version_id") == version["id"]]
            if not appr:
                break
            a = appr[0]
            who = {"manager": "kxmgr", "md": "kxmd"}.get(a.get("required_role"), "kxsales2")
            r = await c.post(f"/v1/approvals/{a['id']}/decision", headers=h[who],
                             json={"decision": "approve", "note": "re-test"})
            decided.append([a.get("kind"), who, r.status_code])
            await asyncio.sleep(4)
        out["approvals"] = decided
    await check(out, t0)


async def check(out: dict[str, object], t0: float) -> None:
    raw = await sent_copy(t0)
    msg = message_from_bytes(raw, policy=policy.default)
    text = msg.get_body(preferencelist=("plain", "html"))
    first = (text.get_content() if text else "").strip().splitlines()[:1]
    pdfs = [p for p in msg.iter_attachments() if p.get_content_type() == "application/pdf"]
    out["subject"] = str(msg["Subject"])
    out["subject_names_the_quotation"] = bool(re.match(r"^Quotation Q-[0-9]{4}-[0-9]{5} ", out["subject"]))
    out["greeting"] = first[0][:60] if first else ""
    out["no_hi_customer"] = "customer" not in (first[0].lower() if first else "")
    out["pdf_attached"] = len(pdfs) == 1
    if pdfs:
        pdf = pdfs[0].get_content()
        (RES / "retest_quote.pdf").write_bytes(pdf)
        txt = subprocess.run(["pdftotext", "-layout", str(RES / "retest_quote.pdf"), "-"], capture_output=True,
                             text=True, stdin=subprocess.DEVNULL).stdout
        to_block = re.search(r"(?s)\bTo\b(.{0,300})", txt)
        out["pdf_to_block_names_buyer"] = bool(to_block and BUYER in to_block.group(1))
        out["pdf_currency_usd"] = "USD" in txt
    out["pass"] = all(out.get(x) for x in ("subject_names_the_quotation", "no_hi_customer",
                                           "pdf_attached", "pdf_to_block_names_buyer"))
    (RES / "email_quote_check.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps(out, ensure_ascii=False))


asyncio.run(main())
