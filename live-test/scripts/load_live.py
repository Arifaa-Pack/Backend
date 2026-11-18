"""Live load test: N concurrent users against the live stack, with per-container resource sampling.

  python load_live.py --users 10 --minutes 8 --label c10_fakeai
Mix: ~50 % website visitors (chat), ~40 % WhatsApp buyers (Meta-signed webhooks, AI reply in the worker),
~10 % staff (TOTP sign-in, deals, deal page, timeline, quotes, inbox, dashboards). Human pacing 5-20 s.
Samples: docker stats (every container of project kxlive), Postgres connections, job backlog / oldest waiting job,
and after the run the WhatsApp reply lag (inbound received -> outbound created) from the database.
Output: results/load_<label>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).parent))
import kxclient as k  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "results"
STAFF = ["kxsales1", "kxsales2", "kxmgr", "kxmd", "kxadmin"]
TEXTS = {
    "en": ["Hello, I need aluminium containers for my restaurant", "Do you have 750 ml containers with lids?",
           "We need about 20,000 pieces per month", "Can you deliver to Muscat?", "Can you print our logo?",
           "What is the lead time?", "Please send me the datasheet", "Thank you, please share an offer"],
    "ar": ["مرحبا، أحتاج حاويات ألمنيوم لمطعمي", "هل لديكم حاويات 750 مل مع أغطية؟", "نحتاج حوالي عشرين ألف قطعة شهريا"],
    "hi": ["नमस्ते, मुझे अपने रेस्टोरेंट के लिए एल्यूमिनियम कंटेनर चाहिए", "क्या ढक्कन वाले 750 ml कंटेनर हैं?"],
    "zh": ["你好，我需要餐厅用的铝箔餐盒", "有带盖的750毫升餐盒吗？"],
}


class M:
    def __init__(self) -> None:
        self.lat: dict[str, list[float]] = defaultdict(list)
        self.codes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    async def call(self, c: httpx.AsyncClient, name: str, method: str, url: str, **kw: Any) -> httpx.Response | None:
        t0 = time.perf_counter()
        try:
            r = await c.request(method, url, **kw)
            self.codes[name][str(r.status_code)] += 1
            self.lat[name].append((time.perf_counter() - t0) * 1000)
            return r
        except httpx.HTTPError as e:
            self.codes[name][type(e).__name__] += 1
            return None

    def summary(self) -> dict[str, Any]:
        out = {}
        for name, v in sorted(self.lat.items()):
            s = sorted(v)
            out[name] = {"n": len(s), "p50_ms": round(statistics.median(s), 1), "p95_ms": round(s[int(len(s) * .95) - 1 if len(s) > 1 else 0], 1),
                         "p99_ms": round(s[max(0, int(len(s) * .99) - 1)], 1), "max_ms": round(s[-1], 1), "codes": dict(self.codes[name])}
        for name in self.codes:
            if name not in out:
                out[name] = {"n": 0, "codes": dict(self.codes[name])}
        return out


async def web_user(i: int, end: float, m: M) -> None:
    ip = f"10.{100 + i // 250}.{i % 250}.{random.randint(1, 250)}"
    async with httpx.AsyncClient(base_url=k.API, timeout=120, headers={"X-Forwarded-For": ip}) as c:
        while time.time() < end:
            lang = random.choice(list(TEXTS))
            r = await m.call(c, "web.session", "POST", "/v1/public/sessions", json={"language": lang, "cookie_consent": True})
            if r is None or r.status_code != 201:
                await asyncio.sleep(10)
                continue
            s = r.json()
            h = {"Authorization": f"Bearer {s['session_token']}"}
            for _ in range(random.randint(3, 6)):
                if time.time() >= end:
                    break
                await m.call(c, "web.message(AI)", "POST", f"/v1/public/sessions/{s['session_id']}/messages", headers=h,
                             json={"text": random.choice(TEXTS[lang]), "language": lang})
                await asyncio.sleep(random.uniform(5, 15))


async def wa_user(i: int, end: float, m: M) -> None:
    phone = f"9715{random.choice([0, 2, 4, 5, 6, 8])}{random.randint(1_000_000, 9_999_999)}"
    lang = random.choice(list(TEXTS))
    async with httpx.AsyncClient(base_url=k.API, timeout=30) as c:
        while time.time() < end:
            raw = k.wa_payload(phone, f"Load Buyer {i}", random.choice(TEXTS[lang]))
            await m.call(c, "wa.webhook", "POST", "/webhooks/whatsapp", content=raw,
                         headers={"X-Hub-Signature-256": k.wa_sig(raw), "Content-Type": "application/json"})
            await asyncio.sleep(random.uniform(8, 20))


async def staff_user(user: str, end: float, m: M) -> None:
    async with httpx.AsyncClient(base_url=k.API, timeout=60) as c:
        t0 = time.perf_counter()
        token = await k.login(c, user)
        m.lat["staff.login(TOTP)"].append((time.perf_counter() - t0) * 1000)
        m.codes["staff.login(TOTP)"]["200"] += 1
        h = {"Authorization": f"Bearer {token}"}
        while time.time() < end:
            r = await m.call(c, "crm.deals", "GET", "/v1/deals", headers=h, params={"limit": 25})
            deals = r.json().get("items", []) if r is not None and r.status_code == 200 else []
            if deals:
                d = random.choice(deals)
                await m.call(c, "crm.deal", "GET", f"/v1/deals/{d['id']}", headers=h)
                await m.call(c, "crm.timeline", "GET", f"/v1/deals/{d['id']}/timeline", headers=h)
                await m.call(c, "crm.quotes", "GET", f"/v1/deals/{d['id']}/quotes", headers=h)
            await m.call(c, "crm.inbox", "GET", "/v1/inbox", headers=h)
            await m.call(c, "crm.contacts", "GET", "/v1/contacts", headers=h, params={"limit": 25})
            if user in ("kxmgr", "kxmd"):
                await m.call(c, "crm.dashboard", "GET", "/v1/dashboards/manager", headers=h)
            await asyncio.sleep(random.uniform(5, 15))




class Sampler(threading.Thread):
    """Polls `docker stats --no-stream` (one call takes about 2 s on Docker Desktop) and the DB every 10 s.
    stdin is closed for every docker call: an inherited stdin makes the docker CLI hang in background shells."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.stats: dict[str, list[tuple[float, float]]] = defaultdict(list)  # name -> (cpu %, mem MiB)
        self.db: list[dict[str, Any]] = []

    @staticmethod
    def _mib(s: str) -> float:
        v = s.split("/")[0].strip()
        for unit, f in (("GiB", 1024), ("MiB", 1), ("KiB", 1 / 1024), ("B", 1 / 1024 / 1024)):
            if v.endswith(unit):
                return float(v[: -len(unit)]) * f
        return 0.0

    @staticmethod
    def _sh(*args: str) -> str:
        try:
            return subprocess.run(list(args), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60).stdout
        except subprocess.TimeoutExpired:
            return ""

    def run(self) -> None:
        last_db = 0.0
        while not self.stop.is_set():
            for line in self._sh("docker", "stats", "--no-stream", "--format", "{{json .}}").splitlines():
                try:
                    j = json.loads(line.strip())
                except ValueError:
                    continue
                name = j.get("Name", "")
                if name.startswith(("kxlive-", "signoz")):
                    self.stats[name].append((float(j["CPUPerc"].rstrip("%") or 0), self._mib(j["MemUsage"])))
            if time.time() - last_db > 10:
                last_db = time.time()
                conns = self._sh("docker", "exec", "kxlive-postgres-1", "psql", "-U", "postgres", "-tAc",
                                 "select (select count(*) from pg_stat_activity where datname like 'pack_%'), "
                                 "(select count(*) from pg_stat_activity where datname like 'pack_%' and state='active')").strip()
                jobs = self._sh("docker", "exec", "kxlive-postgres-1", "psql", "-U", "postgres", "-d", "pack_automation", "-tAc",
                                "select count(*), coalesce(extract(epoch from now()-min(run_at)),0) from "
                                "automation.scheduled_jobs where status='todo' and run_at <= now()").strip()
                try:
                    c_all, c_act = conns.split("|")
                    j_n, j_age = jobs.split("|")
                    self.db.append({"t": time.time(), "pg_conns": int(c_all), "pg_active": int(c_act),
                                    "jobs_due": int(j_n), "oldest_due_s": round(float(j_age), 1)})
                except ValueError:
                    pass
            time.sleep(2)

    def summary(self) -> dict[str, Any]:
        out = {}
        for name, v in sorted(self.stats.items()):
            cpu = [x[0] for x in v]
            mem = [x[1] for x in v]
            out[name] = {"samples": len(v), "cpu_avg_pct": round(statistics.mean(cpu), 1), "cpu_peak_pct": round(max(cpu), 1),
                         "mem_avg_mib": round(statistics.mean(mem), 1), "mem_peak_mib": round(max(mem), 1)}
        tot_cpu = sum(o["cpu_avg_pct"] for n, o in out.items() if n.startswith("kxlive-"))
        tot_mem = sum(o["mem_peak_mib"] for n, o in out.items() if n.startswith("kxlive-"))
        db = {"pg_conns_peak": max((d["pg_conns"] for d in self.db), default=None),
              "pg_active_peak": max((d["pg_active"] for d in self.db), default=None),
              "jobs_due_peak": max((d["jobs_due"] for d in self.db), default=None),
              "oldest_due_job_peak_s": max((d["oldest_due_s"] for d in self.db), default=None)}
        return {"containers": out, "kxlive_total_cpu_avg_pct": round(tot_cpu, 1), "kxlive_total_mem_peak_mib": round(tot_mem, 1),
                "db_and_queue": db, "db_series": self.db}


def reply_lag(since: float) -> dict[str, Any]:
    """WhatsApp: seconds from each inbound message to the next outbound reply on the same thread."""
    sql = f"""
      select extract(epoch from (select min(o.created_at) from automation.outbound_messages o
                                 where o.thread_id = i.thread_id and o.created_at >= i.received_at and o.kind='reply')
                     - i.received_at)
      from automation.inbound_messages i where i.provider='meta_cloud' and i.received_at >= to_timestamp({since})"""
    out = subprocess.run(["docker", "exec", "kxlive-postgres-1", "psql", "-U", "postgres", "-d", "pack_automation", "-tAc", sql],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60).stdout.split()
    vals = sorted(float(x) for x in out if x.strip())
    total = len(out) if out else 0
    blocked = subprocess.run(["docker", "exec", "kxlive-postgres-1", "psql", "-U", "postgres", "-d", "pack_automation", "-tAc",
                              f"select status||':'||coalesce(block_reason,''), count(*) from automation.outbound_messages "
                              f"where created_at >= to_timestamp({since}) group by 1"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60).stdout
    return {"inbound": total, "replied": len(vals),
            "lag_p50_s": round(statistics.median(vals), 2) if vals else None,
            "lag_p95_s": round(vals[int(len(vals) * .95) - 1], 2) if len(vals) > 1 else None,
            "lag_max_s": round(vals[-1], 2) if vals else None, "outbound_by_status": blocked.strip().splitlines()}


async def main(users: int, minutes: float, label: str) -> None:
    n_staff = max(1, min(len(STAFF), round(users * 0.1)))
    n_wa = round(users * 0.4)
    n_web = users - n_staff - n_wa
    m, s = M(), Sampler()
    proxy0 = httpx.get("http://localhost:39100/_stats").json()
    s.start()
    await asyncio.sleep(6)
    start = time.time()
    end = start + minutes * 60
    tasks = [web_user(i, end, m) for i in range(n_web)] + [wa_user(i, end, m) for i in range(n_wa)] + \
            [staff_user(STAFF[i], end, m) for i in range(n_staff)]
    await asyncio.gather(*tasks)
    await asyncio.sleep(20)  # let the worker drain
    s.stop.set()
    res = {"label": label, "users": users, "mix": {"web": n_web, "whatsapp": n_wa, "staff": n_staff}, "minutes": minutes,
           "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start)), "endpoints": m.summary(),
           "resources": s.summary(), "whatsapp_reply": reply_lag(start),
           "llm_proxy": {"before": proxy0, "after": httpx.get("http://localhost:39100/_stats").json()}}
    reqs = sum(v["n"] for v in res["endpoints"].values())
    errs = sum(n for v in res["endpoints"].values() for code, n in v["codes"].items() if not code.startswith(("2", "3")))
    res["totals"] = {"requests": reqs, "errors": errs, "error_rate_pct": round(100 * errs / max(reqs, 1), 2),
                     "throughput_rps": round(reqs / (minutes * 60), 2)}
    (OUT / f"load_{label}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k_: res[k_] for k_ in ("label", "mix", "totals", "whatsapp_reply")}, ensure_ascii=False))
    print(json.dumps(res["endpoints"], ensure_ascii=False)[:1800])
    print(json.dumps({k_: v for k_, v in res["resources"].items() if k_ != "db_series"}, ensure_ascii=False)[:2200])


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--users", type=int, required=True)
    p.add_argument("--minutes", type=float, default=8)
    p.add_argument("--label", required=True)
    a = p.parse_args()
    asyncio.run(main(a.users, a.minutes, a.label))
