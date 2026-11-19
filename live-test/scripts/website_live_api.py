"""Live test of the Website API v2 (WP-C3 / C4) against the running live stack, with the real AI.

    python live-test/scripts/website_live_api.py [--base http://localhost:38000]

What it does, as a visitor would (one session, the real model behind the llm proxy):
  * reads the CMS content the website shows (site, manifest, a product) and checks no price is served;
  * streams chat turns (SSE) and times them: first page action, first words, done;
  * English and Arabic, product codes, families, pages, a mixed product + factory question (specialists),
    a question about price (the AI must not state one);
  * nudges (events), the contact card with the WhatsApp opt-in, the enquiry desk;
  * then reads the CRM's record of the visit straight from Postgres (deal, consent, note).
Writes live-test/results/website_live/api_turns.json and api_summary.md.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
from typing import Any

import httpx

OUT = pathlib.Path(__file__).resolve().parents[1] / "results" / "website_live"
PRICE = re.compile(
    r"(?i)(?:aed|dhs?|dirhams?|usd|\$|درهم)\s*\d|\d[\d.,]*\s*(?:aed|dhs?|dirhams?|درهم|fils|per (?:piece|pc|carton))"
)

TURNS: list[dict[str, Any]] = [
    {"text": "Hi, show me kitchen foil", "context": {"page": "home", "path": "/"}},
    {"text": "do you have 8325?", "context": {"page": "products", "path": "/products?family=foil", "family": "foil"}},
    {
        "text": "I need containers with lids for biryani takeaway, which one fits 1 kg?",
        "context": {"page": "products", "path": "/products"},
    },
    {
        "text": "What sizes and packing does the 8325 come in, is it in stock, and what is your monthly production capacity?",
        "context": {"page": "product", "path": "/products/ac-8325", "product_id": "ac-8325"},
    },
    {"text": "How much is the 8325 per carton?", "context": {"page": "product", "path": "/products/ac-8325"}},
    {"text": "where are your certificates?", "context": {"page": "product", "path": "/products/ac-8325"}},
    {"text": "أرني حاويات ألمنيوم", "context": {"page": "quality", "path": "/ar/quality", "locale": "ar"}},
    {
        "text": "We need about 20,000 pieces a month for our restaurants in Dubai. Can you prepare a quotation?",
        "context": {"page": "products", "path": "/products?family=containers", "family": "containers"},
    },
]


def sse_turn(http: httpx.Client, sid: str, auth: dict[str, str], turn: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    first_action = first_delta = done_at = None
    actions: list[dict[str, Any]] = []
    reply: list[str] = []
    done: dict[str, Any] = {}
    error: dict[str, Any] | None = None
    with http.stream(
        "POST",
        f"/v1/public/sessions/{sid}/messages",
        json=turn,
        headers=auth | {"Accept": "text/event-stream"},
        timeout=180,
    ) as r:
        status = r.status_code
        name = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
                at = round((time.perf_counter() - started) * 1000)
                if name == "action":
                    first_action = first_action or at
                    actions.append(data | {"at_ms": at})
                elif name == "delta":
                    first_delta = first_delta or at
                    reply.append(data["text"])
                elif name == "done":
                    done, done_at = data, at
                elif name == "error":
                    error, done_at = data, at
    text = " ".join(reply)
    return {
        "text": turn["text"],
        "status": status,
        "ms_first_action": first_action,
        "ms_first_words": first_delta,
        "ms_done": done_at,
        "actions": [
            {k: v for k, v in a.items() if k in {"type", "family", "product_id", "page", "section", "layer", "at_ms"}}
            for a in actions
        ],
        "reply": text,
        "handed_over": done.get("handed_over"),
        "error": error,
        "price_in_reply": bool(PRICE.search(text)),
    }


def psql(db: str, sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "kxlive-postgres-1", "psql", "-U", "postgres", "-d", db, "-tAc", sql],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return out.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:38000")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"base": args.base, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    origin = {"Origin": "http://localhost:3100"}
    with httpx.Client(base_url=args.base, headers=origin, timeout=60) as http:
        # 1. what the website reads from the CMS
        content = {}
        for path in ("/v1/content/site", "/v1/content/manifest", "/v1/content/products/ac-8325",
                     "/v1/content/products?family=foil", "/v1/content/sitemap", "/v1/content/redirects"):
            t0 = time.perf_counter()
            r = http.get(path)
            body = r.text
            content[path] = {
                "status": r.status_code,
                "ms": round((time.perf_counter() - t0) * 1000),
                "bytes": len(body),
                "etag": r.headers.get("etag"),
                "cors": r.headers.get("access-control-allow-origin"),
                "price_found": bool(PRICE.search(body)) or '"price"' in body,
            }
        etag = content["/v1/content/manifest"]["etag"]
        if etag:
            again = http.get("/v1/content/manifest", headers={"If-None-Match": etag})
            content["/v1/content/manifest (If-None-Match)"] = {"status": again.status_code}
        report["content"] = content

        # 2. a visitor's chat, streamed
        s = http.post("/v1/public/sessions", json={"language": "en", "voice_consent": True}).json()
        sid, auth = s["session_id"], {"Authorization": f"Bearer {s['session_token']}"}
        report["session"] = {k: s[k] for k in ("language", "voice_available")}
        turns = []
        for turn in TURNS:
            result = sse_turn(http, sid, auth, turn)
            turns.append(result)
            print(f"[{result['ms_first_action']}/{result['ms_done']} ms] {turn['text'][:60]!r} -> "
                  f"{[a['type'] + ':' + str(a.get('family') or a.get('product_id') or a.get('page')) for a in result['actions']]}")
        report["turns"] = turns

        # 3. nudges, the contact card, the enquiry desk
        on_product = {"page": "product", "path": "/products/ac-8342", "product_id": "ac-8342"}
        nudges = [
            http.post(f"/v1/public/sessions/{sid}/events", json={"type": "product_dwell", "context": on_product,
                                                                 "seconds": 25}, headers=auth).json(),
            http.post(f"/v1/public/sessions/{sid}/events", json={"type": "enquiry_add",
                                                                 "context": on_product | {"enquiry_list": ["ac-8342"]}},
                      headers=auth).json(),
        ]
        report["nudges"] = nudges
        card = http.post(
            f"/v1/public/sessions/{sid}/contact",
            json={"name": "Live Test Buyer", "company": "Live Test Kitchens", "phone": "+971500000777",
                  "whatsapp_opt_in": True, "email_opt_in": False,
                  "consent_wording": "Yes, send my quotation and follow-ups on WhatsApp"},
            headers=auth,
        )
        desk = http.post(
            "/v1/public/enquiries",
            json={"session_id": sid, "family": ["containers"], "products": ["ac-8325", "ac-8342"],
                  "quantity": "20,000 a month", "often": "monthly", "city": "Dubai", "region": "UAE",
                  "name": "Live Test Buyer", "company": "Live Test Kitchens", "trade": "Restaurant",
                  "channel": "whatsapp", "phone": "+971500000777", "message": "Weekly deliveries please",
                  "locale": "en"},
            headers=auth,
        )
        report["contact_card"] = {"status": card.status_code, "body": card.json()}
        report["enquiry"] = {"status": desk.status_code, "body": desk.json()}

    # 4. what the CRM holds now
    deal = psql("pack_crm", f"select d.number||' | '||d.source||' | '||d.stage||' | owner='||coalesce(u.full_name,'-') "
                f"from crm.deals d join crm.contacts c on c.id=d.primary_contact_id left join crm.users u on "
                f"u.id=d.owner_user_id where c.visitor_ref='{sid}'")
    consent = psql("pack_crm", f"select channel||' | '||status||' | '||captured_via||' | '||wording from crm.consents "
                   f"where contact_id=(select id from crm.contacts where visitor_ref='{sid}')")
    note = psql("pack_crm", f"select summary from crm.timeline_events where kind='enquiry' and deal_id=(select d.id from "
                f"crm.deals d join crm.contacts c on c.id=d.primary_contact_id where c.visitor_ref='{sid}')")
    msgs = psql("pack_crm", f"select count(*) from crm.timeline_events where kind like 'message%' and deal_id=(select d.id "
                f"from crm.deals d join crm.contacts c on c.id=d.primary_contact_id where c.visitor_ref='{sid}')")
    report["crm"] = {"deal": deal, "consent": consent, "enquiry_note": note, "chat_lines_on_timeline": msgs}
    (OUT / "api_turns.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["crm"], ensure_ascii=False, indent=1))
    leaks = [t["text"] for t in turns if t["price_in_reply"]]
    print("price in a reply:", leaks or "none")
    return 1 if leaks else 0


if __name__ == "__main__":
    sys.exit(main())
