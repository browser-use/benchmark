# /// script
# requires-python = ">=3.12"
# dependencies = ["matplotlib==3.10.8", "numpy==2.4.2"]
# ///
"""Render the independent confirmation, preserving the original two-run plots."""
import collections
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

BASE = Path(__file__).resolve().parent

def main():
    data_dir = BASE / "official_results/confirmation-20260925"
    data = json.loads((data_dir / "summary.json").read_text())
    attempts = [json.loads(s) for s in (data_dir / "attempts.jsonl").read_text().splitlines()]
    assert len(attempts) == 700
    assert len({(r["configuration"], r["task_id"]) for r in attempts}) == 700
    for cfg in data["configurations"]:
        rows = [r for r in attempts if r["configuration"] == cfg["configuration"]]
        counts = dict(collections.Counter(r["classification"] for r in rows))
        assert len(rows) == cfg["attempts"] == 100
        assert counts == cfg["classifications"]
        assert cfg["confirmed_access_percent"] == sum(r["score"] == 1 for r in rows)
    out = BASE / "official_plots"
    out.mkdir(exist_ok=True)
    font = BASE.parent / "fonts/GeistMono-Medium.otf"
    if font.exists():
        font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({"font.family": "Geist Mono" if font.exists() else "monospace", "svg.fonttype": "none"})
    rows = sorted(data["configurations"], key=lambda r: -r["confirmed_access_percent"])
    for theme in ("light", "dark"):
        dark = theme == "dark"
        bg, fg, muted, grid = ("#171717", "#eeeeee", "#b8b8b8", "#363636") if dark else ("#fafafa", "#222222", "#6b6b6b", "#e6e6e6")
        fig, ax = plt.subplots(figsize=(14, 8), facecolor=bg)
        ax.set_facecolor(bg)
        fig.subplots_adjust(left=.32, right=.87, top=.73, bottom=.24)
        fig.text(.055, .90, "STEALTH BENCH V2", fontsize=25, weight="bold", color=fg)
        fig.text(.055, .837, "INDEPENDENT CONFIRMATION / 100 DOMAINS / 60 SECONDS", fontsize=12, color=muted)
        fig.text(.055, .781, "Bars: new run     Circles: original two-run average", fontsize=11, color=fg)
        for y, row in enumerate(rows):
            score = row["confirmed_access_percent"]
            old = row["original_two_repeat"]["confirmed_access_percent"]
            upper = row["possible_access_percent"]
            color = "#ff790e" if row["configuration"] == "cloud" else "#918b9f"
            ax.barh(y, score, height=.52, color=color, zorder=3)
            if upper > score:
                ax.barh(y, upper - score, left=score, height=.52, facecolor="none", edgecolor=muted, hatch="////", lw=.7, zorder=4)
            ax.scatter([old], [y], s=40, facecolor=bg, edgecolor=fg, lw=1.3, zorder=5)
            ax.text(max(score, upper, old) + 1.7, y, f"{score:g}%", va="center", fontsize=12, color=fg)
        ax.set_yticks(range(len(rows)), [r["label"] for r in rows], color=fg, fontsize=12)
        ax.invert_yaxis()
        ax.set_xlim(0, 105)
        ax.set_xticks([0, 20, 40, 60, 80, 100], ["0%", "20%", "40%", "60%", "80%", "100%"])
        ax.tick_params(axis="x", colors=muted, length=0, pad=10)
        ax.tick_params(axis="y", length=0, pad=14)
        ax.xaxis.grid(True, color=grid, zorder=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        fig.text(.055, .155, "Same frozen cohort; selected for historical Cloud vs headful + proxy differences.", fontsize=10, color=fg)
        fig.text(.055, .11, "All 100 tasks retained per provider. Hatching: unresolved access. Luna judges page evidence.", fontsize=10, color=muted)
        fig.text(.055, .065, "Confirmation concurrency: five per provider. See methodology and judge-audit limitations.", fontsize=10, color=muted)
        for suffix in ("png", "svg"):
            path = out / f"stealth_v2_confirmation_{theme}.{suffix}"
            fig.savefig(path, dpi=180, facecolor=bg)
            if suffix == "svg":
                path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n")
        plt.close(fig)

if __name__ == "__main__":
    main()
