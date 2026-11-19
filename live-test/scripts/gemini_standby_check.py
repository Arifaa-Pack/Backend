"""Paid Gemini key check (19 Nov): with Claude down, does the platform's own Gemini standby answer?

The LLM proxy is put in `fail` mode (every Claude call answers 529 overloaded, as in an outage), then a website
visitor asks a question. Expected: a real answer from the standby (Gemini, with the client's paid key), not the
safe reply "Let me check with our sales team". The proxy goes back to `real` mode at the end, whatever happens.
Output: results/gemini_standby_check.json (the reply text and the API's fallback log events; no keys).
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import httpx

API = "http://localhost:38000"
PROXY = "http://localhost:39100"
RES = Path(__file__).resolve().parents[1] / "results"
SAFE = "let me check with our sales team"
IP = {"X-Forwarded-For": "198.51.100.120"}  # its own client address: the day's website limits are per IP


def main() -> None:
    out: dict[str, object] = {}
    t0 = time.time()
    with httpx.Client(base_url=API, timeout=120) as c:
        try:
            httpx.post(f"{PROXY}/_mode", json={"mode": "fail"}, timeout=10)
            s = c.post("/v1/public/sessions", headers=IP,
                       json={"language": "en", "cookie_consent": True, "voice_consent": False}).json()
            hs = {"Authorization": f"Bearer {s['session_token']}", **IP}
            r = c.post(f"/v1/public/sessions/{s['session_id']}/messages", headers=hs,
                       json={"text": "Hello, which aluminium container sizes do you make?", "language": "en"})
            out["status"] = r.status_code
            out["reply"] = r.json().get("reply") if r.status_code == 200 else r.text[:300]
        finally:
            httpx.post(f"{PROXY}/_mode", json={"mode": "real"}, timeout=10)
    out["proxy_stats_after_outage"] = httpx.get(f"{PROXY}/_stats", timeout=10).json()
    since = f"{int(time.time() - t0) + 5}s"
    logs = subprocess.run(["docker", "logs", "--since", since, "kxlive-api-1"], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=60)
    events = []
    for line in (logs.stdout + logs.stderr).splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if any(k in str(e.get("event", "")) for k in ("llm", "model", "fallback", "standby", "gemini")):
            events.append({k: e.get(k) for k in ("event", "level", "model", "provider", "error", "from", "to")})
    out["api_llm_events"] = events
    reply = str(out.get("reply") or "")
    out["answered_by_standby"] = out.get("status") == 200 and bool(reply) and SAFE not in reply.lower()
    (RES / "gemini_standby_check.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("status", "answered_by_standby")}), "|", reply[:300])
    print(json.dumps(events, ensure_ascii=False)[:1500])


main()
