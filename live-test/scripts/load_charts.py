"""Charts + Markdown tables for §9 from results/load_c*_w*.json. Run with a Python that has matplotlib:
  F:/kx-bench/venv/docbuild/Scripts/python.exe live-test/scripts/load_charts.py
Writes results/chart_*.png and results/load_section.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RES = Path(__file__).resolve().parents[1] / "results"
RUNS = [("c10_w3", "10 users"), ("c30_w3", "30 users"), ("c50_w3", "50 users"), ("c50_w10", "50 users, worker ×10"), ("c10_w3_realai", "10 users, real AI")]
data = {k: json.loads((RES / f"load_{k}.json").read_text(encoding="utf-8")) for k, _ in RUNS if (RES / f"load_{k}.json").exists()}
runs = [(k, lbl) for k, lbl in RUNS if k in data]
labels = [lbl for _, lbl in runs]
INK, MUTED = "#1F2937", "#6B7280"
C = ["#2563EB", "#F59E0B", "#10B981", "#8B5CF6", "#EF4444", "#0EA5E9", "#84CC16"]
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": "#D1D5DB", "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED})


def ep(k: str, name: str, field: str) -> float:
    return data[k]["endpoints"].get(name, {}).get(field) or 0.0


def bars(ax, series: dict[str, list[float]], unit: str) -> None:
    n = len(series)
    w = 0.8 / n
    for j, (name, vals) in enumerate(series.items()):
        xs = [i + (j - (n - 1) / 2) * w for i in range(len(labels))]
        b = ax.bar(xs, vals, w * 0.92, label=name, color=C[j % len(C)])
        for x, v in zip(xs, vals):
            ax.text(x, v, f"{v:.0f}" if v >= 10 else f"{v:.1f}", ha="center", va="bottom", fontsize=7, color=INK)
    ax.set_xticks(range(len(labels)), [lb.replace(", ", "\n") for lb in labels])
    ax.set_ylabel(unit)
    ax.legend(frameon=False, fontsize=8, ncol=min(n, 4), loc="upper left")


# 1 latency
fig, axs = plt.subplots(1, 2, figsize=(10, 3.4))
bars(axs[0], {"CRM screens p95": [max(ep(k, n, "p95_ms") for n in ("crm.deals", "crm.deal", "crm.timeline", "crm.inbox")) for k, _ in runs],
              "WhatsApp webhook p95": [ep(k, "wa.webhook", "p95_ms") for k, _ in runs],
              "web session p95": [ep(k, "web.session", "p95_ms") for k, _ in runs]}, "milliseconds")
axs[0].set_title("API response time (no AI)", color=INK, loc="left")
bars(axs[1], {"p50": [ep(k, "web.message(AI)", "p50_ms") / 1000 for k, _ in runs],
              "p95": [ep(k, "web.message(AI)", "p95_ms") / 1000 for k, _ in runs]}, "seconds")
axs[1].set_title("Website chat turn incl. AI (3 s model)", color=INK, loc="left")
fig.tight_layout()
fig.savefig(RES / "chart_latency.png", dpi=150)

# 2 whatsapp reply lag + backlog
fig, axs = plt.subplots(1, 2, figsize=(10, 3.4))
bars(axs[0], {"p50": [data[k]["whatsapp_delivery"]["p50_s"] or 0 for k, _ in runs],
              "p95": [data[k]["whatsapp_delivery"]["p95_s"] or 0 for k, _ in runs]}, "seconds")
axs[0].set_title("WhatsApp: buyer message → reply delivered", color=INK, loc="left")
for j, (k, lbl) in enumerate(runs):
    ser = data[k]["resources"]["db_series"]
    if ser:
        t0 = ser[0]["t"]
        pts = [((d["t"] - t0) / 60, d["jobs_due"]) for d in ser if (d["t"] - t0) / 60 <= data[k]["minutes"]]
        axs[1].plot([x for x, _ in pts], [y for _, y in pts], label=lbl, color=C[j], lw=1.6)
axs[1].set_xlabel("minutes into the run")
axs[1].set_ylabel("jobs waiting (due)")
axs[1].set_title("Background job backlog (during the run)", color=INK, loc="left")
axs[1].legend(frameon=False, fontsize=8)
fig.tight_layout()
fig.savefig(RES / "chart_queue.png", dpi=150)

# 3 resources
names = ["kxlive-api-1", "kxlive-worker-1", "kxlive-beat-1", "kxlive-postgres-1", "kxlive-redis-1", "kxlive-stalwart-1", "kxlive-phoenix-1"]
short = ["API", "Worker", "Beat", "Postgres", "Redis", "Stalwart", "Phoenix"]
fig, axs = plt.subplots(1, 2, figsize=(10, 3.6))
for ax, field, unit, title in ((axs[0], "mem_peak_mib", "MiB", "Memory, peak per container"),
                               (axs[1], "cpu_avg_pct", "% of one core", "CPU, average per container")):
    n = len(runs)
    w = 0.8 / n
    for j, (k, lbl) in enumerate(runs):
        cont = data[k]["resources"]["containers"]
        xs = [i + (j - (n - 1) / 2) * w for i in range(len(names))]
        ax.bar(xs, [cont.get(nm, {}).get(field, 0) for nm in names], w * 0.92, label=lbl, color=C[j])
    ax.set_xticks(range(len(names)), short, rotation=0)
    ax.set_ylabel(unit)
    ax.set_title(title, color=INK, loc="left")
    ax.legend(frameon=False, fontsize=7)
fig.tight_layout()
fig.savefig(RES / "chart_resources.png", dpi=150)

# markdown tables
L = ["| Measure | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]


def row(name: str, vals: list[str]) -> None:
    L.append(f"| {name} | " + " | ".join(vals) + " |")


row("Mix (web / WhatsApp / staff)", [f"{d['mix']['web']} / {d['mix']['whatsapp']} / {d['mix']['staff']}" for d in (data[k] for k, _ in runs)])
row("Duration, requests / errors", [f"{data[k]['minutes']:.0f} min, {data[k]['totals']['requests']} / {data[k]['totals']['errors']} ({data[k]['totals']['error_rate_pct']} %)" for k, _ in runs])
row("Throughput", [f"{data[k]['totals']['throughput_rps']} req/s" for k, _ in runs])
for nm, lbl in (("crm.deals", "CRM deal list p50 / p95"), ("crm.deal", "CRM deal page p50 / p95"), ("crm.inbox", "CRM inbox p50 / p95"),
                ("wa.webhook", "WhatsApp webhook p50 / p95"), ("web.session", "Website session p50 / p95"),
                ("web.message(AI)", "Website AI turn p50 / p95")):
    row(lbl, [f"{ep(k, nm, 'p50_ms'):.0f} / {ep(k, nm, 'p95_ms'):.0f} ms" if ep(k, nm, "p50_ms") < 1000 else
              f"{ep(k, nm, 'p50_ms') / 1000:.1f} / {ep(k, nm, 'p95_ms') / 1000:.1f} s" for k, _ in runs])
row("WhatsApp messages answered", [f"{data[k]['whatsapp_reply']['replied']} / {data[k]['whatsapp_reply']['inbound']}" for k, _ in runs])
row("**WhatsApp reply delivered to buyer** p50 / p95 / max", [f"{data[k]['whatsapp_delivery']['p50_s']} / {data[k]['whatsapp_delivery']['p95_s']} / {data[k]['whatsapp_delivery']['max_s']} s" for k, _ in runs])
row("WhatsApp replies delivered within 60 s", [f"{data[k]['whatsapp_delivery']['within_60s_pct']} %" for k, _ in runs])
row("WhatsApp reply created (AI done) p50 / p95 / max", [f"{data[k]['whatsapp_reply']['lag_p50_s']} / {data[k]['whatsapp_reply']['lag_p95_s']} / {data[k]['whatsapp_reply']['lag_max_s']} s" for k, _ in runs])
row("Job backlog peak / oldest waiting", [f"{data[k]['resources']['db_and_queue']['jobs_due_peak']} / {data[k]['resources']['db_and_queue']['oldest_due_job_peak_s']} s" for k, _ in runs])
row("Postgres connections peak", [str(data[k]["resources"]["db_and_queue"]["pg_conns_peak"]) for k, _ in runs])
for nm, s in zip(names, short):
    row(f"{s}: CPU avg / peak, RAM peak", [
        (lambda c: f"{c.get('cpu_avg_pct', 0)} / {c.get('cpu_peak_pct', 0)} %, {c.get('mem_peak_mib', 0):.0f} MiB")(data[k]["resources"]["containers"].get(nm, {}))
        for k, _ in runs])
row("**Platform total** (all kxlive containers): CPU avg, RAM peak", [f"{data[k]['resources']['kxlive_total_cpu_avg_pct']} % , {data[k]['resources']['kxlive_total_mem_peak_mib'] / 1024:.2f} GiB" for k, _ in runs])
(RES / "load_section.md").write_text("\n".join(L) + "\n", encoding="utf-8")
print("\n".join(L))
