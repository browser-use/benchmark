"""Generate benchmark plots from official_results data."""

import json
from dataclasses import dataclass
from pathlib import Path

import matplotlib.patches as mpatches
import numpy as np
import matplotlib.pyplot as plt
from matplotlib import font_manager

FONT_PATH = Path(__file__).parent / "fonts" / "GeistMono-Medium.otf"
if FONT_PATH.exists():
    font_manager.fontManager.addfont(str(FONT_PATH))
    plt.rcParams["font.family"] = "Geist Mono"

RESULTS_DIR = Path(__file__).parent / "official_results"
OUTPUT_DIR = Path(__file__).parent / "official_plots"
N_BOOTSTRAP = 1000
EXPECTED_TASKS = 100

MODEL_CATEGORIES: dict[str, str] = {
    "bu-ultra": "cloud",
    "bcode-claude-opus-4-7": "browser-harness",
}

DISPLAY_NAMES: dict[str, str] = {
    "bu-ultra": "Cloud\nbu-ultra",
    "ChatBrowserUse-2": "OSS +\nBU LLM",
    "gemini-3-1-pro-preview": "gemini-3-1-pro",
    "grok-4.20-openrouter": "grok-4.20",
    "qwen3.6-plus-openrouter": "qwen3.6-plus",
    "kimi-k2.6-fireworks": "kimi-k2.6",
    "deepseek-v4-pro-fireworks": "deepseek-v4-pro",
    "bcode-claude-opus-4-7": "bcode\nopus-4-7",
    "claude-sonnet-4-6-cch": "ClaudeCode\nsonnet-4-6",
    "claude-opus-4-6-cch": "ClaudeCode\nopus-4-6",
    "claude-opus-4-7-cch": "ClaudeCode\nopus-4-7",
}

CATEGORY_LABELS: dict[str, str] = {
    "cloud": "Cloud",
    "browser-harness": "Browser Harness (OSS)",
    "oss": "Old Harness (OSS)",
}


def get_category(model: str) -> str:
    return MODEL_CATEGORIES.get(model, "oss")


def display_name(model: str) -> str:
    return DISPLAY_NAMES.get(model, model)


def wrap_label(label: str) -> str:
    """Plot-only label normalization.

    If label already contains a newline, treat it as an explicit override.
    Otherwise: split at the end of the leading alphabetic word and remove
    exactly one connecting dash from the remainder (the first dash that
    joins the leading word to whatever follows). Other dashes — typically
    inside dash-separated version numbers — are preserved.

    Examples:
        "gpt-5.5"        -> "gpt\\n5.5"
        "qwen3.6-plus"   -> "qwen\\n3.6plus"
        "claude-opus-4-7"-> "claude\\nopus-4-7"
        "gemini-3-1-pro" -> "gemini\\n3-1-pro"
    """
    if "\n" in label:
        return label
    i = 0
    while i < len(label) and label[i].isalpha():
        i += 1
    if i == 0 or i == len(label):
        return label
    head = label[:i]
    rest = label[i:]
    dash = rest.find("-")
    if dash != -1:
        rest = rest[:dash] + rest[dash + 1 :]
    if not rest:
        return label
    return f"{head}\n{rest}"


@dataclass
class Theme:
    name: str
    background: str
    foreground: str
    border: str
    primary: str
    secondary: str
    muted: str


LIGHT = Theme(
    name="light",
    background="#FAFAFA",
    foreground="#1A1A1A",
    border="#E5E5E5",
    primary="#FE670C",
    secondary="#2563EB",
    muted="#9CA3AF",
)

DARK = Theme(
    name="dark",
    background="#0A0A0A",
    foreground="#FAFAFA",
    border="#2A2A2A",
    primary="#FE670C",
    secondary="#2563EB",
    muted="#6B7280",
)

CATEGORY_COLOR_ATTR: dict[str, str] = {
    "cloud": "primary",
    "browser-harness": "secondary",
    "oss": "muted",
}


def build_colors(names: list[str], theme: Theme) -> dict[str, str]:
    """Color by category: primary for cloud, secondary for hybrid, muted for oss."""
    return {
        name: getattr(theme, CATEGORY_COLOR_ATTR[get_category(name)]) for name in names
    }


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
    ax.tick_params(colors=theme.foreground, which="both", labelsize=18)
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


def add_category_legend(ax, theme: Theme):
    """Add a color-coded legend showing Cloud / OSS + Cloud LLM / Open Source."""
    patches = [
        mpatches.Patch(
            color=getattr(theme, CATEGORY_COLOR_ATTR[cat]),
            label=CATEGORY_LABELS[cat],
        )
        for cat in ["cloud", "browser-harness", "oss"]
    ]
    legend = ax.legend(
        handles=patches,
        loc="upper right",
        fontsize=18,
        frameon=True,
        facecolor=theme.background,
        edgecolor=theme.border,
        labelcolor=theme.foreground,
    )
    legend.get_frame().set_alpha(0.9)


def plot_accuracy_by_model(results: dict[str, list[dict]], theme: Theme):
    """Bar chart colored by category: cloud, hybrid, oss."""
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
                "display": display_name(model),
                "mean": mean * 100,
                "err_lo": (mean - lo) * 100,
                "err_hi": (hi - mean) * 100,
                "color": colors[model],
            }
        )

    if not data:
        return

    data.sort(key=lambda x: x["mean"], reverse=True)

    fig, ax = plt.subplots(figsize=(18, 9))
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
            fontsize=18,
            color=theme.foreground,
            fontweight="bold",
        )

    ax.set_xticks(x)
    ax.set_xticklabels(
        [wrap_label(d["display"]) for d in data],
        rotation=0,
        ha="center",
        fontsize=14,
    )
    ax.set_ylabel("Score (%)", fontsize=20)

    vals = [d["mean"] for d in data]
    ax.set_ylim(max(0, min(vals) - 7), max(vals) + 5)

    apply_theme(ax, theme)
    add_category_legend(ax, theme)
    fig.tight_layout()
    ax.text(
        0.5,
        0.95,
        "BU Bench V1: Success Rate",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=28,
        color=theme.foreground,
    )
    fig.savefig(
        OUTPUT_DIR / f"accuracy_by_model_{theme.name}.png",
        dpi=150,
        facecolor=theme.background,
    )
    plt.close(fig)


def plot_accuracy_vs_throughput(results: dict[str, list[dict]], theme: Theme):
    """Scatter plot colored by category with display names."""
    colors = build_colors(list(results.keys()), theme)
    data = []
    for model, runs in results.items():
        accs = compute_accuracies(runs)
        tph = compute_tasks_per_hour(runs)
        if not accs or not tph:
            continue
        acc_mean, acc_lo, acc_hi = bootstrap_ci(accs)
        tph_mean, tph_lo, tph_hi = bootstrap_ci(tph)
        cat = get_category(model)
        data.append(
            {
                "model": model,
                "display": display_name(model),
                "color": colors[model],
                "is_cloud": cat == "cloud",
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
    fig, ax = plt.subplots(figsize=(18, 10))

    for d in sorted(data, key=lambda d: d["is_cloud"]):
        size = 18 if d["is_cloud"] else 12
        zorder = 10 if d["is_cloud"] else 5
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
        ax.annotate(
            d["display"],
            (d["tph"], d["acc"]),
            textcoords="offset points",
            xytext=(10, 6),
            fontsize=16,
            color=d["color"],
        )

    ax.set_xlabel("Tasks per Hour", fontsize=20)
    ax.set_ylabel("Score (%)", fontsize=20)

    accs = [d["acc"] for d in data]
    ax.set_ylim(max(0, min(accs) - 10), max(accs) + 10)

    apply_theme(ax, theme)
    add_category_legend(ax, theme)
    fig.tight_layout()
    ax.text(
        0.5,
        0.95,
        "BU Bench V1: Success vs. Throughput",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=28,
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
