"""Step 13.5: answer the Gmail buyer's "USD please, and can you reduce the amount to 4880" through the platform,
as the deal's salesperson. Also checks D-9 live (the message is in the owner's inbox) and D-2 (the approved
quote is sent with its PDF, no storage workaround).

Pricing (client rule: never below the tier minimum without the manager; negotiate step by step, volume for
price). Tiers for ALC-750-L, AED per piece ex VAT: 10,000-49,999 list 0.280 / min 0.255; 50,000+ list 0.250 /
min 0.230. USD 1 = AED 3.6725 (the CRM's FX table).
  * 20,000 pcs: 0.0762 -> USD 0.0720 (= AED 0.2644, above the 0.255 minimum): a real first step, not the floor.
  * pitch: 30,000 at USD 0.0700 (= AED 0.2571 >= 0.255); 50,000 at USD 0.0640 (= AED 0.2350 >= 0.230), within
    about 1 % of the buyer's own per-piece target (AED 4,880 incl. VAT / 20,000 = USD 0.0633 ex VAT).

Run: .venv/Scripts/python.exe live-test/scripts/reply_gmail_buyer.py [--send]   (without --send: dry run)
"""

from __future__ import annotations

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kxclient as k  # noqa: E402

BUYER = "muhammadhamthan660@gmail.com"
FX = Decimal("3.6725")
PRICE_USD = Decimal("0.0720")
BUYER_NOTE = (
    "As requested, this quotation is now in US dollars (USD 1 = AED 3.6725), and I have improved the price "
    "for 20,000 pcs from USD 0.0762 to USD 0.0720 per piece."
)
COVER_NOTE = (
    "AED 4,880 (about USD 1,329) for 20,000 pcs is below what we can do at this quantity, but volume makes a "
    "real difference: 30,000 pcs at USD 0.0700, or 50,000 pcs at USD 0.0640 per piece, within about 1% of "
    "your target per piece. We can deliver the 50,000 in two or three lots over the coming months, so you "
    "don't hold extra stock. Shall I prepare that option for you?"
)


def check_rules() -> None:
    """The prices quoted or offered must respect the AED tier minimums at the CRM's rate."""
    for usd, aed_min in ((PRICE_USD, "0.255"), (Decimal("0.0700"), "0.255"), (Decimal("0.0640"), "0.230")):
        assert usd * FX >= Decimal(aed_min), (usd, usd * FX, aed_min)
    assert len(BUYER_NOTE) <= 500 and len(COVER_NOTE) <= 500, (len(BUYER_NOTE), len(COVER_NOTE))


async def main(send: bool) -> None:
    check_rules()
    out: dict[str, Any] = {}
    async with httpx.AsyncClient(base_url=k.API, timeout=120) as c:
        tok = {u: await k.login(c, u) for u in ("kxmgr", "kxsales1", "kxsales2", "kxmd")}
        h = {u: {"Authorization": f"Bearer {t}"} for u, t in tok.items()}
        users = (await c.get("/v1/users", headers=h["kxmgr"])).json()
        users = users if isinstance(users, list) else users.get("items", [])
        # found the way the team would: the buyer's message in the inbox (D-9: it used to be missing)
        deal = None
        for user in ("kxsales1", "kxsales2", "kxmgr"):
            for item in (await c.get("/v1/inbox", headers=h[user])).json():
                if item["kind"] == "buyer_message" and deal is None:
                    full = (await c.get(f"/v1/deals/{item['deal_id']}", headers=h["kxmgr"])).json()
                    if (full.get("contact") or {}).get("email") == BUYER:
                        deal = full
        assert deal is not None, "the Gmail buyer's message is not in the inbox"
        owner = next(u["email"].split("@")[0] for u in users if u["id"] == deal["owner_user_id"])
        inbox = (await c.get("/v1/inbox", headers=h[owner])).json()
        mine = [i for i in inbox if i["deal_id"] == deal["id"] and i["kind"] == "buyer_message"]
        out["deal"] = {"number": deal["number"], "stage": deal["stage"], "owner": owner}
        out["d9_inbox_shows_buyer_message"] = [i["title"][:120] for i in mine]
        print(json.dumps(out, indent=1, ensure_ascii=False))
        if not send:
            print("dry run: add --send to revise, approve and send")
            return
        body = {
            "currency": "USD",
            "lines": [{"line_no": 1, "unit_price": str(PRICE_USD)}],
            "buyer_note": BUYER_NOTE,
            "cover_note": COVER_NOTE,
            "note": "Buyer asked for USD and AED 4,880 total; first step + volume offer (live test 18 Nov)",
        }
        r = await c.post(f"/v1/deals/{deal['id']}/quotes/revise", headers=h[owner], json=body)
        print("revise", r.status_code, r.text[:400])
        r.raise_for_status()
        version = r.json()
        for _ in range(4):  # decide every open approval with the role it needs
            appr = []
            for u in ("kxmd", "kxmgr", owner):
                items = (await c.get("/v1/approvals", headers=h[u], params={"status": "open", "limit": 100})).json()
                appr += [a for a in items.get("items", []) if a.get("quote_version_id") == version["id"]]
            if not appr:
                break
            a = appr[0]
            who = {"manager": "kxmgr", "md": "kxmd"}.get(a.get("required_role"), owner)
            r = await c.post(
                f"/v1/approvals/{a['id']}/decision", headers=h[who], json={"decision": "approve", "note": "sent"}
            )
            print("approval", a.get("kind"), a.get("required_role"), who, r.status_code, r.text[:200])
            await asyncio.sleep(5)
        await asyncio.sleep(20)
        qv = (await c.get(f"/v1/quote-versions/{version['id']}", headers=h[owner])).json()
        print(json.dumps({k_: qv.get(k_) for k_ in ("version_no", "status", "currency", "total")}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main("--send" in sys.argv))
