"""API part 3: dynamic security probes against the live API (the external-pentest view).
Each probe records: expected safe behaviour, what happened, PASS/FAIL, evidence. Output: results/sec_probes.json
Uses only the live-test accounts and data; locks/unlocks only the test user kxsales2.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).parent))
import kxclient as k  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "results"
R: list[dict[str, Any]] = []


def reset_throttle() -> None:
    """Harness (M3, 19 Nov): forget the sign-in counters in Redis between probe groups, like a fresh environment.
    Every probe of the platform comes from this one PC, so without this one group's throttle answers the next."""
    run = dict(capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    keys = subprocess.run(["docker", "exec", "kxlive-redis-1", "redis-cli", "--scan", "--pattern", "kx:pack:*auth*"], **run).stdout.split()
    if keys:
        subprocess.run(["docker", "exec", "kxlive-redis-1", "redis-cli", "del", *keys], **run)


def from_ip(addr: str) -> dict[str, str]:
    """The client address the proxy saw (the stack trusts X-Forwarded-For, as production trusts Cloudflare)."""
    return {"X-Forwarded-For": addr}


def rec(area: str, probe: str, ok: bool, expected: str, got: Any, sev: str = "") -> None:
    R.append({"area": area, "probe": probe, "pass": bool(ok), "expected": expected, "got": got, "severity_if_fail": sev})
    print(f"[{'PASS' if ok else 'FAIL'}] {area}: {probe} -> {json.dumps(got, ensure_ascii=False, default=str)[:260]}", flush=True)


async def main() -> None:
    st = k.load_state()
    async with httpx.AsyncClient(base_url=k.API, timeout=60) as c:
        T = {u: await k.login(c, u) for u in ("kxmgr", "kxsales1", "kxadmin", "kxmd")}
        H = {u: {"Authorization": f"Bearer {t}"} for u, t in T.items()}

        # ---- 1 transport / headers / CORS / info leakage
        for path in ("/healthz", "/v1/auth/me", "/docs"):
            r = await c.get(path, headers=H["kxsales1"])
            hd = {h: r.headers.get(h) for h in ("strict-transport-security", "x-content-type-options", "x-frame-options",
                                                  "content-security-policy", "referrer-policy", "server", "cache-control")}
            missing = [h for h in ("x-content-type-options", "x-frame-options", "content-security-policy") if not hd[h]]
            rec("headers", f"security headers on {path}", not missing, "nosniff, frame-deny, CSP (HSTS at the edge)",
                {"missing": missing, **hd}, "Low")
        r = await c.get("/docs")
        # 19 Nov: off in production, on locally and on staging (testers). This stack runs KX_ENV=local, so the
        # production answer is read from the app built with KX_ENV=production (no server needed).
        prod = subprocess.run([sys.executable, "-c", "from arifaa_pack.api import create_app; a = create_app(); "
                               "print(a.docs_url, a.redoc_url, a.openapi_url)"], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env={**os.environ, "KX_ENV": "production"}, timeout=120)
        prod_urls = prod.stdout.strip().splitlines()[-1] if prod.stdout.strip() else prod.stderr[-200:]
        rec("info", "Swagger /docs and /openapi.json public", prod_urls == "None None None", "hidden in production",
            {"this_stack_local": {"docs": r.status_code, "openapi": (await c.get("/openapi.json")).status_code},
             "production_docs_redoc_openapi": prod_urls}, "Low")
        r = await c.options("/v1/deals", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET",
                                                  "Access-Control-Request-Headers": "authorization"})
        rec("cors", "preflight from an unknown origin", r.headers.get("access-control-allow-origin") is None,
            "no Access-Control-Allow-Origin", {"status": r.status_code, "acao": r.headers.get("access-control-allow-origin")}, "High")
        r = await c.options("/v1/deals", headers={"Origin": "http://localhost:3001", "Access-Control-Request-Method": "GET"})
        rec("cors", "preflight from the configured CRM origin", r.headers.get("access-control-allow-origin") == "http://localhost:3001",
            "allowed", {"status": r.status_code, "acao": r.headers.get("access-control-allow-origin"),
                        "credentials": r.headers.get("access-control-allow-credentials")})
        r = await c.post("/v1/auth/login", content=b"{bad json", headers={"content-type": "application/json"})
        rec("info", "malformed JSON -> no stack trace", r.status_code == 422 and "Traceback" not in r.text and "File \"" not in r.text,
            "422 problem+json", {"status": r.status_code, "body": r.text[:200]}, "Medium")
        r = await c.request("TRACE", "/healthz")
        rec("info", "TRACE method disabled", r.status_code in (405, 404), "405", r.status_code, "Low")
        jw = (await c.get("/.well-known/jwks.json")).json()
        rec("auth", "JWKS publishes only public keys", all("d" not in kk and "p" not in kk for kk in jw.get("keys", [])),
            "no private members", {"keys": [{x: kk.get(x) for x in ("kty", "alg", "kid", "use")} for kk in jw.get("keys", [])]}, "Critical")
        alg = json.loads(base64.urlsafe_b64decode(T["kxsales1"].split(".")[0] + "=="))
        rec("auth", "access token algorithm", alg.get("alg") in ("RS256", "ES256", "EdDSA"), "asymmetric", alg)

        # ---- 2 login abuse: enumeration, lockout, throttling (M3: per IP and per account + IP)
        reset_throttle()
        t0 = time.perf_counter(); r1 = await c.post("/v1/auth/login", json={"email": "nobody@kairoxaitech.com", "password": "x" * 12}, headers=from_ip("198.51.100.10")); a = time.perf_counter() - t0
        t0 = time.perf_counter(); r2 = await c.post("/v1/auth/login", json={"email": "kxsales2@kairoxaitech.com", "password": "x" * 12}, headers=from_ip("198.51.100.10")); b = time.perf_counter() - t0
        rec("auth", "account enumeration (unknown user vs wrong password)", r1.status_code == r2.status_code and r1.text == r2.text
            and abs(a - b) < 0.25, "same status, body and time", {"unknown": [r1.status_code, r1.text[:90], round(a, 3)],
                                                                  "known": [r2.status_code, r2.text[:90], round(b, 3)]}, "Medium")
        await k.login(c, "kxsales2", headers=from_ip("198.51.100.20"))  # the staff member's office (a known IP)
        codes = []
        for i in range(12):
            r = await c.post("/v1/auth/login", json={"email": "kxsales2@kairoxaitech.com", "password": f"wrong-{i}-pw"}, headers=from_ip("203.0.113.7"))
            codes.append(r.status_code)
        r = await c.post("/v1/auth/login", json={"email": "kxsales2@kairoxaitech.com", "password": st["kxsales2"]["password"]}, headers=from_ip("203.0.113.7"))
        rec("auth", "lockout after repeated wrong passwords (KX_AUTH__MAX_FAILED_ATTEMPTS=5)", r.status_code != 200,
            "correct password refused while locked", {"wrong_attempt_codes": codes, "correct_pw_after": r.status_code,
                                                      "body": r.text[:150]}, "High")
        other = await c.post("/v1/auth/login", json={"email": "kxsales2@kairoxaitech.com", "password": st["kxsales2"]["password"]}, headers=from_ip("198.51.100.20"))
        rec("auth", "anyone can lock a staff account by failing its login (lockout DoS)", other.status_code == 200,
            "the attacker's IP is locked; the staff member still signs in from the office IP they use (M3)",
            {"attacker_codes": codes, "staff_from_other_ip": other.status_code}, "Medium")
        stuff = [(await c.post("/v1/auth/login", json={"email": f"user{i}@kairoxaitech.com", "password": "Winter2026!"}, headers=from_ip("203.0.113.9"))).status_code
                 for i in range(30)]
        rec("auth", "credential stuffing: 30 different e-mails from one IP", 429 in stuff, "429 per IP", stuff, "Medium")
        users = (await c.get("/v1/users", headers=H["kxadmin"])).json()
        users = users if isinstance(users, list) else users.get("items", [])
        s2 = next(u for u in users if u["email"].startswith("kxsales2@"))
        typo = [(await c.post("/v1/auth/login", json={"email": "kxsales2@kairoxaitech.com", "password": f"typo-{i}"},
                               headers=from_ip("198.51.100.20"))).status_code for i in range(6)]  # mistyped at the office
        r = await c.post(f"/v1/users/{s2['id']}/unlock", headers=H["kxadmin"])
        r2 = await c.post("/v1/auth/login", json={"email": "kxsales2@kairoxaitech.com", "password": st["kxsales2"]["password"]}, headers=from_ip("198.51.100.20"))
        rec("auth", "admin unlock restores the account (the staff member locked out at the office signs in at once)",
            r.status_code < 300 and r2.status_code == 200 and 429 in typo, "unlocked", {"typos": typo, "unlock": r.status_code, "login_after": r2.status_code})

        # ---- 3 MFA / reset code guessing
        reset_throttle()
        r = await c.post("/v1/auth/login", json={"email": "kxsales1@kairoxaitech.com", "password": st["kxsales1"]["password"]}, headers=from_ip("198.51.100.30"))
        ch = r.json().get("challenge_token")
        guesses = [(await c.post("/v1/auth/mfa/totp", json={"challenge_token": ch, "code": f"{i:06d}"})).status_code for i in range(8)]
        good = await c.post("/v1/auth/mfa/totp", json={"challenge_token": ch, "code": k.totp(st["kxsales1"]["totp_secret"])})
        rec("auth", "TOTP guessing: challenge dies after a few wrong codes", good.status_code != 200, "challenge burned",
            {"wrong": guesses, "right_code_after": good.status_code}, "High")
        s1 = next(u for u in users if u["email"].startswith("kxsales1@"))
        await c.post(f"/v1/users/{s1['id']}/unlock", headers=H["kxadmin"])
        reset_throttle()
        rr = await c.post("/v1/auth/password/reset/start", json={"email": "nobody@kairoxaitech.com"}, headers=from_ip("198.51.100.40"))
        rk = await c.post("/v1/auth/password/reset/start", json={"email": "kxsales2@kairoxaitech.com"}, headers=from_ip("198.51.100.40"))
        rec("auth", "reset start does not reveal whether an account exists", rr.status_code == rk.status_code and rr.text == rk.text,
            "same answer", [rr.status_code, rr.text[:80], rk.status_code, rk.text[:80]], "Medium")
        reset_throttle()
        t0 = time.time()
        await c.post("/v1/auth/password/reset/start", json={"email": "kxsales2@kairoxaitech.com"}, headers=from_ip("198.51.100.50"))
        real = await k.latest_code("kxsales2", t0)
        fin = [(await c.post("/v1/auth/password/reset/finish", json={"email": "kxsales2@kairoxaitech.com", "code": f"{(int(real) + i + 1) % 10**6:06d}",
                                                                     "new_password": "Brute-" + uuid.uuid4().hex}, headers=from_ip("198.51.100.50"))).status_code for i in range(6)]
        newpw = "Live-" + uuid.uuid4().hex
        ok_real = await c.post("/v1/auth/password/reset/finish", json={"email": "kxsales2@kairoxaitech.com", "code": real, "new_password": newpw}, headers=from_ip("198.51.100.50"))
        if ok_real.status_code < 300:
            st2 = k.load_state(); st2["kxsales2"]["password"] = newpw; k.save_state(st2)
        rec("auth", "reset code: real code refused after 6 wrong guesses (KX_AUTH__CODE_MAX_TRIES=5)", ok_real.status_code >= 400,
            "code burned", {"wrong": fin, "real_code_after": ok_real.status_code}, "High")
        await c.post(f"/v1/users/{s2['id']}/unlock", headers=H["kxadmin"])

        # ---- 4 session / token lifecycle
        reset_throttle()
        tok = await k.login(c, "kxsales1")
        r = await c.post("/v1/auth/logout", headers={"Authorization": f"Bearer {tok}"})
        r2 = await c.get("/v1/auth/me", headers={"Authorization": f"Bearer {tok}"})
        rec("auth", "access token after logout", r2.status_code == 401, "rejected (or short TTL accepted risk)",
            {"logout": r.status_code, "me_after_logout": r2.status_code, "access_ttl_s": 900}, "Low")
        T["kxsales1"] = await k.login(c, "kxsales1"); H["kxsales1"] = {"Authorization": f"Bearer {T['kxsales1']}"}

        # ---- 5 authorisation edge cases
        r = await c.patch(f"/v1/users/{next(u['id'] for u in users if u['email'].startswith('kxadmin@'))}",
                          headers=H["kxadmin"], json={"role": "md"})
        me = (await c.get("/v1/auth/me", headers=H["kxadmin"])).json()
        rec("authz", "IT admin promotes itself to MD", r.status_code in (400, 403, 422) and me.get("role") == "admin",
            "refused", {"status": r.status_code, "body": r.text[:150], "role_now": me.get("role")}, "High")
        comp = await c.post("/v1/companies", headers=H["kxmgr"], json={"name": "=HYPERLINK(\"http://evil.example\",\"click\")",
                                                                       "country_code": "AE"})
        cid = comp.json().get("id") if comp.status_code < 300 else None
        # L3 (19 Nov): a company nobody is linked to yet is open to every salesperson (so a new contact can be
        # linked to it); it becomes a customer once a contact on a deal belongs to it. Link it to a contact on a
        # deal another person owns, then let kxsales1 try to rename it.
        me1 = (await c.get("/v1/auth/me", headers=H["kxsales1"])).json()
        dl = (await c.get("/v1/deals", headers=H["kxmgr"], params={"limit": 100})).json()
        items = dl["items"] if isinstance(dl, dict) else dl
        other = next((d for d in items if d.get("owner_user_id") and d["owner_user_id"] != me1["id"]), None)
        if other is None and items:  # load-test deals are unassigned: give one to kxsales2 (test stack only)
            s2_id = next(u["id"] for u in users if u["email"].startswith("kxsales2@"))
            rr = await c.post(f"/v1/deals/{items[0]['id']}/reassign", headers=H["kxmgr"], json={"user_id": s2_id})
            other = items[0] if rr.status_code < 300 else None
        if cid and other:
            ct = (await c.get(f"/v1/deals/{other['id']}", headers=H["kxmgr"])).json()["contact"]["id"]
            await c.patch(f"/v1/contacts/{ct}", headers=H["kxmgr"], json={"company_id": cid})
        r = await c.patch(f"/v1/companies/{cid}", headers=H["kxsales1"], json={"name": "Renamed by another salesperson"}) if cid and other else None
        rec("authz", "salesperson edits a company that is not on their deals", r is not None and r.status_code in (403, 404),
            "403", {"create": comp.status_code, "patch": r.status_code if r else None}, "Medium")
        if cid:
            await c.patch(f"/v1/companies/{cid}", headers=H["kxmgr"], json={"name": "=HYPERLINK(\"http://evil.example\",\"click\")"})
        for fmt in ("csv", "xlsx"):
            r = await c.get("/v1/exports/companies", headers=H["kxmgr"], params={"format": fmt})
            body = r.content.decode("utf-8", "ignore") if fmt == "csv" else r.content.decode("latin-1", "ignore")
            raw = '=HYPERLINK' in body and "'=HYPERLINK" not in body
            rec("injection", f"CSV/XLSX formula injection in {fmt} export", r.status_code == 200 and not raw,
                "cell escaped with a leading quote", {"status": r.status_code, "formula_unescaped": raw}, "Medium")
        for q in ("' OR 1=1--", "\" ; DROP TABLE crm.deals;--", "%' UNION SELECT password_hash FROM crm.user_credentials--",
                  "<script>alert(1)</script>", "a" * 5000, "\u0000", "${7*7}{{7*7}}"):
            codes = []
            for p_, prm in (("/v1/contacts", "q"), ("/v1/companies", "q"), ("/v1/deals", "sku")):
                try:
                    codes.append((await c.get(p_, headers=H["kxmgr"], params={prm: q})).status_code)
                except httpx.HTTPError as e:
                    codes.append(f"disconnect:{type(e).__name__}")
            rec("injection", f"search payload {q[:30]!r}", all(isinstance(x, int) and x < 500 for x in codes), "no 5xx, no data", codes, "High")
        for lim in ("0", "-1", "100000", "abc"):
            r = await c.get("/v1/deals", headers=H["kxmgr"], params={"limit": lim})
            n = len(r.json().get("items", [])) if r.status_code == 200 else None
            rec("bounds", f"/v1/deals?limit={lim}", r.status_code == 422 or (n is not None and n <= 200), "422 or capped",
                {"status": r.status_code, "items": n}, "Low")
        r = await c.get("/v1/deals", headers=H["kxmgr"], params={"cursor": "not-a-cursor'--"})
        rec("bounds", "garbage pagination cursor", r.status_code in (400, 422), "4xx", r.status_code, "Low")

        # ---- 6 public website chat abuse
        ip = "10.66.0.1"
        r = await c.post("/v1/public/sessions", headers={"X-Forwarded-For": ip}, json={"language": "en", "cookie_consent": True})
        s = r.json()
        r2 = await c.post("/v1/public/sessions", headers={"X-Forwarded-For": "10.66.0.2"}, json={"language": "en", "cookie_consent": True})
        s2 = r2.json()
        r = await c.post(f"/v1/public/sessions/{s2['session_id']}/messages", headers={"Authorization": f"Bearer {s['session_token']}"},
                         json={"text": "hi", "language": "en"})
        rec("public", "session token of A used on session B", r.status_code in (401, 403, 404), "refused", r.status_code, "High")
        r = await c.post(f"/v1/public/sessions/{s['session_id']}/messages", headers={"Authorization": f"Bearer {s['session_token']}",
                         "X-Forwarded-For": ip}, json={"text": "x" * 1_000_000, "language": "en"})
        rec("public", "1 MB chat message", r.status_code in (413, 422), "413/422 before the AI", r.status_code, "Medium")
        burst = []
        for i in range(25):
            rs = await c.post("/v1/public/sessions", headers={"X-Forwarded-For": "10.66.9.9"}, json={"language": "en", "cookie_consent": True})
            burst.append(rs.status_code)
        rec("public", "session creation burst from one IP (25)", 429 in burst, "429 (rate limit)", burst, "Medium")
        r = await c.post("/v1/public/unsubscribe", params={"t": "forged.token.value"})
        rec("public", "forged unsubscribe token", r.status_code in (400, 401, 403, 404, 422), "refused", r.status_code, "Medium")
        r = await c.post("/webhooks/cms", json={"event": "publish", "id": "x"})
        rec("webhook", "CMS webhook without secret", r.status_code in (401, 403), "refused", r.status_code, "Medium")
        r = await c.post(f"/webhooks/telnyx/status/{uuid.uuid4()}", params={"t": "forged"}, json={"data": {"event_type": "call.answered"}})
        rec("webhook", "Telnyx callback with forged token", r.status_code in (401, 403, 404), "refused", r.status_code, "Medium")
        r = await c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "123"})
        rec("webhook", "WhatsApp verify with a wrong verify token", r.status_code in (401, 403), "refused", r.status_code, "Medium")

        # ---- 7 identify takeover (review H1): visitor claims an existing WhatsApp buyer's phone
        deals = (await c.get("/v1/deals", headers=H["kxmd"], params={"limit": 100})).json()["items"]
        victim = None
        for d in deals:
            full = (await c.get(f"/v1/deals/{d['id']}", headers=H["kxmd"])).json()
            ct = full.get("contact") or {}
            if ct.get("whatsapp_e164") and not full.get("opted_out_at") and not ct.get("email"):
                victim = (full, ct)
                break
        if victim:
            full, ct = victim
            r = await c.post("/v1/public/sessions", headers={"X-Forwarded-For": "10.66.7.7"}, json={"language": "en", "cookie_consent": True})
            sa = r.json()
            ri = await c.post(f"/v1/public/sessions/{sa['session_id']}/identify", headers={"Authorization": f"Bearer {sa['session_token']}",
                              "X-Forwarded-For": "10.66.7.7"}, json={"phone": ct["whatsapp_e164"], "email": "attacker@evil.example",
                                                                       "name": "Attacker"})
            await asyncio.sleep(4)
            after = (await c.get(f"/v1/deals/{full['id']}", headers=H["kxmd"])).json().get("contact") or {}
            changed = after.get("email") == "attacker@evil.example" or after.get("full_name") == "Attacker"
            rec("public", "anonymous identify with a real buyer's phone overwrites that buyer's contact", not changed,
                "buyer contact untouched (needs verification)", {"identify": ri.status_code, "victim_deal": full["number"],
                "contact_before": {x: ct.get(x) for x in ("full_name", "email")},
                "contact_after": {x: after.get(x) for x in ("full_name", "email")}}, "High")
        else:
            rec("public", "identify takeover", False, "a WhatsApp buyer to test with", "no candidate deal", "High")

    (OUT / "sec_probes.json").write_text(json.dumps(R, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps({"pass": sum(x["pass"] for x in R), "fail": sum(not x["pass"] for x in R)}))


if __name__ == "__main__":
    asyncio.run(main())
