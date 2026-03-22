"""Generate benchmark plots from official_results data."""

import json
import colorsys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib import font_manager

FONT_PATH = Path(__file__).parent / "fonts" / "GeistMono-Medium.otf"
if FONT_PATH.exists():
    font_manager.fontManager.addfont(str(FONT_PATH))
    plt.rcParams["font.family"] = "Geist Mono"

RESULTS_DIR = Path(__file__).parent / "bu_bench" / "official_results"
OUTPUT_DIR = Path(__file__).parent / "bu_bench" / "official_plots"
N_BOOTSTRAP = 1000
EXPECTED_TASKS = 100
HIGHLIGHT_MODELS = {"bu-max"}


@dataclass
class Theme:
    name: str
    background: str
    foreground: str
    border: str
    primary: str


LIGHT = Theme(
    name="light",
    background="#FAFAFA",
    foreground="#1A1A1A",
    border="#E5E5E5",
    primary="#F97316",
)

DARK = Theme(
    name="dark",
    background="#0A0A0A",
    foreground="#FAFAFA",
    border="#2A2A2A",
    primary="#FB923C",
)


def index_to_color(index: int, total: int, theme: Theme) -> str:
    """Evenly-spaced hue colors, solid and vibrant."""
    hue = index / total
    if theme.name == "dark":
        sat, light = 0.30, 0.40
    else:
        sat, light = 0.35, 0.50
    r, g, b = colorsys.hls_to_rgb(hue, light, sat)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def build_colors(names: list[str], theme: Theme) -> dict[str, str]:
    """Primary for highlighted models, evenly-spaced hues for the rest."""
    colors = {}
    others = sorted([n for n in names if n not in HIGHLIGHT_MODELS])
    for i, name in enumerate(others):
        colors[name] = index_to_color(i, len(others), theme)
    for name in names:
        if name in HIGHLIGHT_MODELS:
            colors[name] = theme.primary
    return colors


def load_results() -> dict[str, list[dict]]:
    results = {}
    for f in RESULTS_DIR.glob("*.json"):
        model = f.stem.split("_model_")[-1]
        runs = json.loads(f.read_text())
        valid = [r for r in runs if r["tasks_completed"] == EXPECTED_TASKS]
        if len(valid) < len(runs):
            skipped = len(runs) - len(valid)
            print(f"WARNING: Skipped {skipped} incomplete runs for {model}")
        if valid:
            results[model] = valid
    return results


def compute_accuracies(runs: list[dict]) -> list[float]:
    return [
        r["tasks_successful"] / r["tasks_completed"]
        for r in runs
        if r["tasks_completed"] > 0
    ]


def compute_tasks_per_hour(runs: list[dict]) -> list[float]:
    return [
        3600 * r["tasks_completed"] / r["total_duration"]
        for r in runs
        if r["tasks_completed"] > 0 and r["total_duration"] > 0
    ]


def bootstrap_ci(
    values: list[float], n: int = N_BOOTSTRAP
) -> tuple[float, float, float]:
    arr = np.array(values)
    means = [
        np.mean(np.random.choice(arr, size=len(arr), replace=True)) for _ in range(n)
    ]
    return (
        float(np.mean(arr)),
        float(np.percentile(means, 2.5)),
        float(np.percentile(means, 97.5)),
    )


def apply_theme(ax, theme: Theme):
    ax.set_facecolor(theme.background)
    ax.figure.set_facecolor(theme.background)
    ax.tick_params(colors=theme.foreground, which="both", labelsize=9)
    ax.xaxis.label.set_color(theme.foreground)
    ax.yaxis.label.set_color(theme.foreground)
    ax.title.set_color(theme.foreground)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(theme.border)
    ax.spines["left"].set_color(theme.border)
    ax.yaxis.grid(True, color=theme.border, linestyle="-", linewidth=0.5, alpha=0.5)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)


def plot_accuracy_by_model(results: dict[str, list[dict]], theme: Theme):
    """Bar chart with evenly-spaced hue colors. Highlighted models get primary."""
    colors = build_colors(list(results.keys()), theme)
    data = []
    for model, runs in results.items():
        accs = compute_accuracies(runs)
        if not accs:
            continue
        mean, lo, hi = bootstrap_ci(accs)
        data.append(
            {
                "model": model,
                "mean": mean * 100,
                "err_lo": (mean - lo) * 100,
                "err_hi": (hi - mean) * 100,
                "color": colors[model],
            }
        )

    if not data:
        return

    data.sort(key=lambda x: x["mean"], reverse=True)

    fig, ax = plt.subplots(figsize=(12, 6))
    x = np.arange(len(data))
    err_color = "#666666" if theme.name == "light" else "#888888"

    ax.bar(
        x,
        [d["mean"] for d in data],
        yerr=[[d["err_lo"] for d in data], [d["err_hi"] for d in data]],
        capsize=3,
        color=[d["color"] for d in data],
        edgecolor="none",
        ecolor=err_color,
        width=0.7,
    )

    for i, d in enumerate(data):
        ax.text(
            i,
            d["mean"] + d["err_hi"] + 1.0,
            f"{d['mean']:.1f}%",
            ha="center",
            va="bottom",
            fontsize=9,
            color=theme.foreground,
            fontweight="bold",
        )

    ax.set_xticks(x)
    ax.set_xticklabels([d["model"] for d in data], rotation=35, ha="right", fontsize=9)
    ax.set_ylabel("Score (%)", fontsize=10)

    vals = [d["mean"] for d in data]
    ax.set_ylim(max(0, min(vals) - 10), max(vals) + 10)

    apply_theme(ax, theme)
    fig.tight_layout()
    ax.text(
        0.5,
        0.95,
        "BU Bench V1: Success Rate",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=16,
        color=theme.foreground,
    )
    fig.savefig(
        OUTPUT_DIR / f"accuracy_by_model_{theme.name}.png",
        dpi=150,
        facecolor=theme.background,
    )
    plt.close(fig)


def plot_accuracy_vs_throughput(results: dict[str, list[dict]], theme: Theme):
    """Scatter plot. Highlighted models in primary, others in hue colors. Labels on points."""
    colors = build_colors(list(results.keys()), theme)
    data = []
    for model, runs in results.items():
        accs = compute_accuracies(runs)
        tph = compute_tasks_per_hour(runs)
        if not accs or not tph:
            continue
        acc_mean, acc_lo, acc_hi = bootstrap_ci(accs)
        tph_mean, tph_lo, tph_hi = bootstrap_ci(tph)
        data.append(
            {
                "model": model,
                "color": colors[model],
                "highlight": model in HIGHLIGHT_MODELS,
                "acc": acc_mean * 100,
                "acc_lo": (acc_mean - acc_lo) * 100,
                "acc_hi": (acc_hi - acc_mean) * 100,
                "tph": tph_mean,
                "tph_lo": tph_mean - tph_lo,
                "tph_hi": tph_hi - tph_mean,
            }
        )

    if not data:
        return

    err_color = "#666666" if theme.name == "light" else "#888888"
    fig, ax = plt.subplots(figsize=(12, 7))

    legend_items = []

    # Plot non-highlighted first, then highlighted on top
    for d in sorted(data, key=lambda d: d["highlight"]):
        size = 12 if d["highlight"] else 8
        zorder = 10 if d["highlight"] else 5
        ax.errorbar(
            d["tph"],
            d["acc"],
            xerr=[[d["tph_lo"]], [d["tph_hi"]]],
            yerr=[[d["acc_lo"]], [d["acc_hi"]]],
            fmt="o",
            capsize=3,
            color=d["color"],
            ecolor=err_color,
            markersize=size,
            zorder=zorder,
        )
        # Label next to point
        ax.annotate(
            d["model"],
            (d["tph"], d["acc"]),
            textcoords="offset points",
            xytext=(8, 4),
            fontsize=8,
            color=d["color"],
        )
        legend_items.append((d["model"], d["color"]))

    ax.set_xlabel("Tasks per Hour", fontsize=10)
    ax.set_ylabel("Score (%)", fontsize=10)

    accs = [d["acc"] for d in data]
    ax.set_ylim(max(0, min(accs) - 10), max(accs) + 10)

    apply_theme(ax, theme)
    fig.tight_layout()
    ax.text(
        0.5,
        0.95,
        "BU Bench V1: Success vs. Throughput",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=16,
        color=theme.foreground,
    )
    fig.savefig(
        OUTPUT_DIR / f"accuracy_vs_throughput_{theme.name}.png",
        dpi=150,
        facecolor=theme.background,
    )
    plt.close(fig)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = load_results()

    print(f"Loaded {len(results)} models:")
    for model, runs in sorted(results.items()):
        accs = compute_accuracies(runs)
        mean = np.mean(accs) * 100 if accs else 0
        print(f"  {model}: {mean:.1f}% ({len(runs)} runs)")

    for theme in [LIGHT, DARK]:
        plot_accuracy_by_model(results, theme)
        plot_accuracy_vs_throughput(results, theme)

    print(f"Saved plots to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
