"""Post-hoc: WhatsApp delivery lag per load run = reply SENT time - buyer message received time (same thread),
for every buyer message received inside the run window. Adds `whatsapp_delivery` to each results/load_*.json."""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

RES = Path(__file__).resolve().parents[1] / "results"


def q(sql: str) -> list[str]:
    return subprocess.run(["docker", "exec", "kxlive-postgres-1", "psql", "-U", "postgres", "-d", "pack_automation", "-tAc", sql],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120).stdout.split()


for f in sorted(RES.glob(sys.argv[1] if len(sys.argv) > 1 else "load_c*_w*.json")):  # e.g. "load_c*_r2.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    start = time.mktime(time.strptime(d["started"], "%Y-%m-%d %H:%M:%S"))
    end = start + d["minutes"] * 60
    vals = q(f"""select coalesce(extract(epoch from (select min(o.sent_at) from automation.outbound_messages o
                  where o.thread_id = i.thread_id and o.kind = 'reply' and o.created_at >= i.received_at) - i.received_at)::text, 'unsent')
                from automation.inbound_messages i where i.provider = 'meta_cloud'
                and i.received_at between to_timestamp({start}) and to_timestamp({end})""")
    sent = sorted(float(v) for v in vals if v != "unsent")
    pct = lambda p: round(sent[min(len(sent) - 1, int(len(sent) * p))], 1) if sent else None  # noqa: E731
    d["whatsapp_delivery"] = {"messages": len(vals), "reply_sent": len(sent), "never_sent": vals.count("unsent"),
                              "p50_s": pct(0.5), "p95_s": pct(0.95), "max_s": round(sent[-1], 1) if sent else None,
                              "within_60s_pct": round(100 * sum(v <= 60 for v in sent) / max(len(vals), 1), 1)}
    f.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f.name, d["whatsapp_delivery"])
