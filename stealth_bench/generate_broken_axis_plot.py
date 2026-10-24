# /// script
# requires-python = ">=3.12"
# dependencies = ["matplotlib==3.10.8", "numpy==2.4.2"]
# ///
"""A visibly broken-axis view of the unchanged, complete two-repeat comparison."""
import json
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from generate_plots import LABELS

ROOT = Path(__file__).resolve().parent


def main():
    summary = json.loads((ROOT / "official_results/summary.json").read_text())
    attempts = [json.loads(s) for s in (ROOT / "official_results/attempts.jsonl").read_text().splitlines()]
    assert len({(r["configuration"], r["repetition"], r["task_id"]) for r in attempts}) == len(attempts)
    rows = sorted(summary["configurations"], key=lambda r: -r["confirmed_access_percent"])
    for row in rows:
        selected = [a for a in attempts if a["configuration"] == row["configuration"]]
        assert len(selected) == row["attempts"] == 200
        assert dict(Counter(a["classification"] for a in selected)) == row["classifications"]
        assert row["confirmed_access_percent"] == sum(a["score"] == 1 for a in selected) / 2
        assert not 20 < row["confirmed_access_percent"] < 65
    font = ROOT.parent / "fonts/GeistMono-Medium.otf"
    if font.exists():
        font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({"font.family": "Geist Mono" if font.exists() else "monospace", "svg.fonttype": "none"})
    for theme in ("light", "dark"):
        dark = theme == "dark"
        bg, fg, muted, grid = ("#111111", "#f6f6f6", "#b7b7b7", "#313131") if dark else ("#fafafa", "#202020", "#666666", "#e5e5e5")
        fig = plt.figure(figsize=(16, 10.5), facecolor=bg)
        # Both segments use the same pixels per percentage point.
        left = fig.add_axes([.32, .23, .176, .56], facecolor=bg)
        right = fig.add_axes([.522, .23, .352, .56], facecolor=bg, sharey=left)
        fig.text(.055, .925, "STEALTH BENCH V2", fontsize=29, weight="bold", color=fg)
        fig.text(.055, .875, "100 DOMAINS / 60 SECONDS / TWO REPEATS", fontsize=13, color=muted)
        fig.text(.055, .824, "Confirmed website access", fontsize=15, color=fg)
        fig.text(.885, .824, "R1 / R2", fontsize=11, color=muted)
        for y, row in enumerate(rows):
            cfg, score = row["configuration"], row["confirmed_access_percent"]
            upper = row["possible_access_percent"]
            reps = [r["confirmed_access_percent"] for r in row["repetitions"]]
            color = "#f87820" if cfg == "cloud" else "#a9a9a9" if cfg.startswith("head") else "#8f899f"
            for ax in (left, right):
                ax.barh(y, score, height=.54, color=color, zorder=3)
                if upper > score:
                    ax.barh(y, upper-score, left=score, height=.54, facecolor="none", edgecolor=muted, hatch="////", lw=.6, zorder=4)
                ax.plot(reps, [y, y], color=fg, lw=1, zorder=5)
                ax.scatter(reps, [y, y], s=20, facecolor=bg, edgecolor=fg, lw=1, zorder=6)
            ax = left if score <= 20 else right
            ax.text(max(score, upper, *reps)+1.1, y, f"{score:g}%", va="center", fontsize=12, color=fg, clip_on=False)
            right.text(106.3, y, f"{reps[0]:g} / {reps[1]:g}", va="center", fontsize=11, color=muted, clip_on=False)
            if score >= 65:
                for ax, edge in ((left, 20), (right, 65)):
                    ax.plot([edge-.5, edge+.5], [y+.22, y-.22], color=bg, lw=4, clip_on=True, zorder=7)
        left.set_xlim(0, 20)
        right.set_xlim(65, 105)
        left.set_ylim(len(rows)-.45, -.65)
        left.set_yticks(range(len(rows)), [LABELS[r["configuration"]] for r in rows], color=fg, fontsize=12)
        right.tick_params(axis="y", left=False, labelleft=False)
        left.set_xticks([0, 10, 20], ["0%", "10%", "20%"])
        right.set_xticks([65, 75, 85, 95, 100], ["65%", "75%", "85%", "95%", "100%"])
        for ax in (left, right):
            ax.tick_params(axis="x", colors=muted, length=0, pad=10)
            ax.tick_params(axis="y", length=0, pad=15)
            ax.xaxis.grid(True, color=grid, lw=.8, zorder=0)
            for spine in ax.spines.values():
                spine.set_visible(False)
        for ax, x in ((left, 1), (right, 0)):
            for y in (0, 1):
                ax.plot([x-.018, x+.018], [y-.012, y+.012], transform=ax.transAxes, color=muted, lw=1.5, clip_on=False)
        fig.text(.055, .16, "AXIS BREAK: 20–65% omitted. Both visible segments use the same scale.", color=fg, fontsize=11)
        fig.text(.055, .12, "All 100 domains retained. Dots: repeat scores. Hatching: unresolved access.", color=muted, fontsize=10)
        fig.text(.055, .082, "Selected for historical Cloud vs headful + proxy gaps; not representative of the web.", color=muted, fontsize=10)
        fig.text(.055, .043, "2026-09-25 · Original two-run results, unchanged · Full-axis chart and methodology linked in README", color=muted, fontsize=9)
        for ext in ("png", "svg"):
            p = ROOT / "official_plots" / f"stealth_v2_broken_axis_{theme}.{ext}"
            fig.savefig(p, dpi=180, facecolor=bg)
            if ext == "svg":
                p.write_text("\n".join(s.rstrip() for s in p.read_text().splitlines())+"\n")
        plt.close(fig)


if __name__ == "__main__":
    main()
