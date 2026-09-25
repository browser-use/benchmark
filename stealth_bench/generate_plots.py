# /// script
# requires-python = ">=3.12"
# dependencies = ["matplotlib==3.10.8", "numpy==2.4.2"]
# ///
"""Plot exact Stealth V2 results; no task filtering, imputation or rescaling."""
import json
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parent
FONT = ROOT.parent / "fonts/GeistMono-Medium.otf"
if FONT.exists():
    font_manager.fontManager.addfont(str(FONT))
    plt.rcParams["font.family"] = "Geist Mono"
plt.rcParams["svg.fonttype"] = "none"

LABELS = {
    "cloud": "Browser Use Cloud",
    "cloud_no_solver": "Browser Use Cloud · solver off",
    "browserbase": "Browserbase · standard",
    "browserbase_verified": "Browserbase · Verified",
    "kernel": "Kernel",
    "anchor": "Anchor",
    "hyperbrowser": "Hyperbrowser",
    "browserless": "Browserless",
    "browserless_solver": "Browserless",
    "steel": "Steel",
    "headful_proxy": "Chromium headful + US proxy",
    "headful_direct": "Chromium headful",
    "headless_proxy": "Chromium headless + US proxy",
    "headless_direct": "Chromium headless",
}


def main():
    data = json.loads((ROOT / "official_results/summary.json").read_text())
    attempts = [
        json.loads(line)
        for line in (ROOT / "official_results/attempts.jsonl").read_text().splitlines()
        if line.strip()
    ]
    keys = {(r["configuration"], r["repetition"], r["task_id"]) for r in attempts}
    assert len(keys) == len(attempts), "Duplicate attempts"
    expected_tasks = {r["task_id"] for r in attempts}
    assert len(expected_tasks) == 100
    assert {r["configuration"] for r in attempts} == {r["configuration"] for r in data["configurations"]}
    for config in data["configurations"]:
        selected = [r for r in attempts if r["configuration"] == config["configuration"]]
        counts = Counter(r["classification"] for r in selected)
        assert dict(counts) == config["classifications"]
        assert counts["accessible"] == config["confirmed_access"]
        assert counts["uncertain"] + counts["judge_or_capture_error"] == config["unresolved"]
        assert config["possible_access_percent"] == (
            config["confirmed_access"] + config["unresolved"]
        ) / 2
        for rep in (1, 2):
            subset = [r for r in selected if r["repetition"] == rep]
            assert len(subset) == 100 and {r["task_id"] for r in subset} == expected_tasks
            repeated = next(r for r in config["repetitions"] if r["repetition"] == rep)
            assert sum(r["classification"] == "accessible" for r in subset) == repeated["confirmed_access"]
        for result in selected:
            category = result["classification"]
            expected = None if category in {"uncertain", "judge_or_capture_error"} else int(category == "accessible")
            assert result["score"] == expected
    rows = sorted(data["configurations"], key=lambda r: (-r["confirmed_access_percent"], r["configuration"]))
    for row in rows:
        assert row["attempts"] == 200
        assert len(row["repetitions"]) == 2
        assert all(r["attempts"] == 100 for r in row["repetitions"])
        assert row["confirmed_access_percent"] == row["confirmed_access"] / 2
    out = ROOT / "official_plots"
    out.mkdir(exist_ok=True)
    for theme in ("light", "dark"):
        dark = theme == "dark"
        bg, fg = ("#0A0A0A", "#FAFAFA") if dark else ("#FAFAFA", "#1A1A1A")
        muted, grid = ("#ACACAC", "#292929") if dark else ("#666666", "#E4E4E4")
        orange = "#FB923C" if dark else "#F97316"
        other = "#77718F" if dark else "#8D879F"
        control = "#505050" if dark else "#B6B6B6"
        fig = plt.figure(figsize=(16, 10.5), facecolor=bg)
        ax = fig.add_axes([.32, .22, .54, .58], facecolor=bg)
        fig.text(.055, .93, "STEALTH BENCH V2", color=fg, fontsize=29, weight="bold")
        fig.text(.055, .883, "100 DOMAINS  /  60 SECONDS  /  TWO REPEATS", color=muted, fontsize=13)
        fig.text(.055, .832, "Luna-judged website access", color=fg, fontsize=15)
        fig.text(.875, .832, "R1 / R2", color=muted, fontsize=11)
        for y, row in enumerate(rows):
            cfg = row["configuration"]
            score = row["confirmed_access_percent"]
            color = orange if cfg == "cloud" else control if cfg.startswith("head") else other
            ax.barh(y, score, height=.56, color=color, zorder=3)
            reps = [r["confirmed_access_percent"] for r in row["repetitions"]]
            ax.plot(reps, [y, y], color=fg, lw=1.2, zorder=5)
            ax.scatter(reps, [y, y], s=16, facecolors=bg, edgecolors=fg, lw=.9, zorder=6)
            upper = row["possible_access_percent"]
            if upper > score:
                ax.barh(y, upper - score, left=score, height=.56,
                        facecolor="none", edgecolor=muted, hatch="////", lw=.5, zorder=4)
            label = f"{score:g}%"
            ax.text(max(upper, score, *reps) + 1.7, y, label, va="center", fontsize=12, color=fg)
            ax.text(111, y, f"{reps[0]:g} / {reps[1]:g}", va="center",
                    fontsize=11, color=muted, clip_on=False)
        ax.set_yticks(range(len(rows)), [LABELS[r["configuration"]] for r in rows], color=fg, fontsize=12)
        ax.invert_yaxis()
        ax.set_xlim(0, 105)
        ax.set_xticks([0, 20, 40, 60, 80, 100], ["0%", "20%", "40%", "60%", "80%", "100%"])
        ax.tick_params(axis="x", colors=muted, labelsize=10, length=0, pad=10)
        ax.tick_params(axis="y", length=0, pad=16)
        ax.xaxis.grid(True, color=grid, linewidth=.8, zorder=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        fig.text(.055, .14, "Selected for historical Cloud vs headful + proxy gaps; not representative of the web.",
                 color=fg, fontsize=10)
        missing = sum(r["unresolved"] for r in rows)
        fig.text(.055, .108, f"All 100 tasks retained. Dots: repeat scores. Hatched extensions: unresolved access ({missing} attempts).",
                 color=muted, fontsize=10)
        fig.text(.055, .076, "Independent page-evidence judge. Configuration and audit limitations in the methodology.",
                 color=muted, fontsize=10)
        fig.text(.055, .039, data["measurement_date"] + "  ·  browser-use/benchmark", color=muted, fontsize=9)
        for suffix in ("png", "svg"):
            path = out / f"stealth_v2_{theme}.{suffix}"
            fig.savefig(path, dpi=180, facecolor=bg)
            if suffix == "svg":
                path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n")
        plt.close(fig)


if __name__ == "__main__":
    main()
