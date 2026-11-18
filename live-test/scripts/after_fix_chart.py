"""Before / after the 18-19 Nov fixes: WhatsApp replies delivered within 60 s and p95 delivery time per load level.
Run with a Python that has matplotlib: F:/kx-bench/venv/docbuild/Scripts/python.exe live-test/scripts/after_fix_chart.py
Reads results/load_c{10,30,50}_w3.json (before), load_c*_w3a10.json (after, production settings) and
load_c*_w3a10_cap10.json (after, recipient cap raised: pure capacity). Writes results/chart_after_fixes.png.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RES = Path(__file__).resolve().parents[1] / "results"
LEVELS = (10, 30, 50)
SERIES = (
    ("Before the fixes", "w3", "#9CA3AF"),
    ("After, production settings", "w3a10", "#2563EB"),
    ("After, capacity (cap raised)", "w3a10_cap10", "#10B981"),
)
INK, MUTED = "#1F2937", "#6B7280"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.edgecolor": "#D1D5DB", "xtick.color": MUTED,
                     "ytick.color": MUTED, "axes.labelcolor": INK})


def value(n: int, key: str, field: str) -> float | None:
    f = RES / f"load_c{n}_{key}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))["whatsapp_delivery"][field]


fig, axs = plt.subplots(1, 2, figsize=(10, 3.4))
for ax, field, unit, title in ((axs[0], "within_60s_pct", "% of WhatsApp replies", "Delivered within 60 s"),
                               (axs[1], "p95_s", "seconds", "p95 time to deliver a WhatsApp reply")):
    w = 0.8 / len(SERIES)
    for j, (label, key, color) in enumerate(SERIES):
        xs = [i + (j - 1) * w for i in range(len(LEVELS))]
        vals = [value(n, key, field) for n in LEVELS]
        ax.bar([x for x, v in zip(xs, vals) if v is not None], [v for v in vals if v is not None], w * 0.92,
               color=color, label=label)
        for x, v in zip(xs, vals):
            if v is not None:
                ax.text(x, v, f"{v:.0f}", ha="center", va="bottom", fontsize=7, color=INK)
            else:
                ax.text(x, 1, "n/a", ha="center", va="bottom", fontsize=6, color=MUTED)
    ax.set_xticks(range(len(LEVELS)), [f"{n} users" for n in LEVELS])
    ax.set_ylabel(unit)
    ax.set_title(title, color=INK, loc="left")
axs[0].axhline(95, color="#EF4444", lw=0.8, ls="--")
axs[0].text(1.27, 91, "target 95 %", color="#EF4444", fontsize=7, ha="center", va="top")
axs[0].legend(frameon=False, fontsize=7, loc="lower left")
fig.tight_layout()
fig.savefig(RES / "chart_after_fixes.png", dpi=150)
print("written", RES / "chart_after_fixes.png")
