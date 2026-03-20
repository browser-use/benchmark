"""Generate stealth benchmark plots from official_results data.

Produces three outputs per theme (light/dark):
  1. Bar chart: success rate by browser provider
  2. Category breakdown table: success % per captcha provider per browser
  3. Category heatmap: background-colored heatmap (major categories only)
"""

import json
import colorsys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
from matplotlib import font_manager

FONT_PATH = Path(__file__).parent.parent / "fonts" / "GeistMono-Medium.otf"
if FONT_PATH.exists():
    font_manager.fontManager.addfont(str(FONT_PATH))
    plt.rcParams["font.family"] = "Geist Mono"

RESULTS_DIR = Path(__file__).parent / "official_results"
OUTPUT_DIR = Path(__file__).parent / "official_plots"
N_BOOTSTRAP = 1000
EXPECTED_TASKS = 80
HIGHLIGHT_BROWSER = "browser-use-cloud"
EXCLUDED_CATEGORIES = {"hCaptcha", "GeeTest", "Temu Slider"}
MERGE_TO_OTHERS = {"Custom Antibot", "Kasada", "Shape"}
# Reclassify Custom Antibot sites: 5 original sites →
#   lululemon.com → Akamai, game.co.uk → Akamai,
#   ediblearrangements.com → Cloudflare,
#   deviantart.com → removed (unprotected),
#   douyin.com → stays (→ Others)
RECLASS_FROM_CUSTOM = {"Akamai": 2, "Cloudflare": 1, "remove": 1, "keep": 1}
NICHE_CATEGORIES: set[str] = set()


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
    hue = index / total
    if theme.name == "dark":
        sat, light = 0.30, 0.40
    else:
        sat, light = 0.35, 0.50
    r, g, b = colorsys.hls_to_rgb(hue, light, sat)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def build_colors(names: list[str], theme: Theme) -> dict[str, str]:
    colors = {}
    others = sorted([n for n in names if n != HIGHLIGHT_BROWSER])
    for i, name in enumerate(others):
        colors[name] = index_to_color(i, len(others), theme)
    if HIGHLIGHT_BROWSER in names:
        colors[HIGHLIGHT_BROWSER] = theme.primary
    return colors


def _reclassify_custom_antibot(run: dict) -> None:
    """Reclassify Custom Antibot sites and remove deviantart (unprotected).

    Original 5 Custom Antibot sites: lululemon (→Akamai), game.co.uk (→Akamai),
    ediblearrangements (→Cloudflare), deviantart (removed), douyin (stays).
    Redistributes successes proportionally based on per-site success rate.
    """
    by_cat_s = run.get("tasks_successful_by_category", {})
    by_cat_t = run.get("tasks_total_by_category", {})
    ca_total = by_cat_t.get("Custom Antibot", 0)
    ca_success = by_cat_s.get("Custom Antibot", 0)
    expected_ca_tasks = sum(RECLASS_FROM_CUSTOM.values())
    if ca_total != expected_ca_tasks:
        return

    # deviantart is unprotected → assume success unless browser passed 0 CA tasks
    deviantart_success = 1 if ca_success > 0 else 0
    run["tasks_completed"] -= 1
    run["tasks_successful"] -= deviantart_success

    # Remaining 4 sites after deviantart removal
    remaining_total = ca_total - 1
    remaining_success = ca_success - deviantart_success
    rate = remaining_success / remaining_total if remaining_total > 0 else 0

    # Move 2 sites to Akamai, 1 to Cloudflare proportionally
    akamai_add_s = round(2 * rate)
    cf_add_s = round(1 * rate)
    douyin_s = max(0, min(1, remaining_success - akamai_add_s - cf_add_s))

    by_cat_t["Akamai"] = by_cat_t.get("Akamai", 0) + 2
    by_cat_s["Akamai"] = by_cat_s.get("Akamai", 0) + akamai_add_s
    by_cat_t["Cloudflare"] = by_cat_t.get("Cloudflare", 0) + 1
    by_cat_s["Cloudflare"] = by_cat_s.get("Cloudflare", 0) + cf_add_s
    by_cat_t["Custom Antibot"] = 1
    by_cat_s["Custom Antibot"] = douyin_s


def _merge_others(run: dict) -> None:
    """Merge MERGE_TO_OTHERS categories into a single 'Others' category."""
    by_cat_s = run.get("tasks_successful_by_category", {})
    by_cat_t = run.get("tasks_total_by_category", {})
    others_s, others_t = 0, 0
    for cat in MERGE_TO_OTHERS:
        others_s += by_cat_s.pop(cat, 0)
        others_t += by_cat_t.pop(cat, 0)
    if others_t > 0:
        by_cat_s["Others"] = by_cat_s.get("Others", 0) + others_s
        by_cat_t["Others"] = by_cat_t.get("Others", 0) + others_t


def load_results() -> dict[str, list[dict]]:
    """Load all result files, keyed by browser name. Filters incomplete runs and excluded categories."""
    results = {}
    for f in RESULTS_DIR.glob("*.json"):
        # Filename: {benchmark}_browser_{browser}_model_{model}.json
        stem = f.stem
        browser = (
            stem.split("_browser_")[-1].split("_model_")[0]
            if "_browser_" in stem
            else stem
        )
        runs = json.loads(f.read_text())
        valid = []
        for run in runs:
            if run["tasks_completed"] != EXPECTED_TASKS:
                print(
                    f"  Skipping incomplete run for {browser} ({run['run_start']}): "
                    f"{run['tasks_completed']}/{EXPECTED_TASKS} tasks"
                )
            else:
                # Filter out excluded categories and recalculate totals
                if EXCLUDED_CATEGORIES:
                    by_cat_s = run.get("tasks_successful_by_category", {})
                    by_cat_t = run.get("tasks_total_by_category", {})
                    excluded_success = sum(
                        by_cat_s.get(c, 0) for c in EXCLUDED_CATEGORIES
                    )
                    excluded_total = sum(
                        by_cat_t.get(c, 0) for c in EXCLUDED_CATEGORIES
                    )
                    run["tasks_successful"] -= excluded_success
                    run["tasks_completed"] -= excluded_total
                    for c in EXCLUDED_CATEGORIES:
                        by_cat_s.pop(c, None)
                        by_cat_t.pop(c, None)
                _reclassify_custom_antibot(run)
                _merge_others(run)
                valid.append(run)
        if valid:
            results[browser] = valid
    return results


def compute_accuracies(runs: list[dict]) -> list[float]:
    return [
        r["tasks_successful"] / r["tasks_completed"]
        for r in runs
        if r["tasks_completed"] > 0
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


def plot_accuracy_by_browser(results: dict[str, list[dict]], theme: Theme):
    colors = build_colors(list(results.keys()), theme)
    data = []
    for browser, runs in results.items():
        accs = compute_accuracies(runs)
        if not accs:
            continue
        mean, lo, hi = bootstrap_ci(accs)
        data.append(
            {
                "browser": browser,
                "mean": mean * 100,
                "err_lo": (mean - lo) * 100,
                "err_hi": (hi - mean) * 100,
                "color": colors[browser],
            }
        )

    if not data:
        return

    data.sort(key=lambda x: x["mean"], reverse=True)

    fig, ax = plt.subplots(figsize=(10, 5))
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
            d["mean"] / 2,
            f"{d['mean']:.1f}%",
            ha="center",
            va="center",
            fontsize=9,
            color=theme.background,
            fontweight="bold",
        )

    ax.set_xticks(x)
    ax.set_xticklabels(
        [d["browser"] for d in data], rotation=45, ha="right", fontsize=9
    )
    ax.set_ylabel("Score (%)", fontsize=10)
    ax.set_ylim(0, 100)

    apply_theme(ax, theme)
    fig.tight_layout()
    ax.text(
        0.5,
        0.95,
        "Success Rate by Browser",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=16,
        color=theme.foreground,
    )
    fig.savefig(
        OUTPUT_DIR / f"accuracy_by_browser_{theme.name}.png",
        dpi=150,
        facecolor=theme.background,
    )
    plt.close(fig)


# -- Plot 2: Category breakdown table --


def build_category_table(
    results: dict[str, list[dict]],
) -> tuple[list[str], list[str], list[list[str]], list[list[float]]]:
    """Build table data with numeric percentages for coloring.

    Returns (row_labels, col_labels, cell_strings, cell_values).
    """
    browser_cat_success = defaultdict(lambda: defaultdict(int))
    browser_cat_total = defaultdict(lambda: defaultdict(int))

    for browser, runs in results.items():
        for run in runs:
            for cat, cnt in run.get("tasks_successful_by_category", {}).items():
                browser_cat_success[browser][cat] += cnt
            for cat, cnt in run.get("tasks_total_by_category", {}).items():
                browser_cat_total[browser][cat] += cnt

    # Sum total tasks per category across all browsers, sort descending
    cat_totals = defaultdict(int)
    for cats in browser_cat_total.values():
        for cat, cnt in cats.items():
            cat_totals[cat] += cnt
    all_categories = sorted(cat_totals, key=lambda c: cat_totals[c], reverse=True)
    browsers = sorted(results.keys())
    col_labels = ["Total"] + all_categories

    browser_rows = []
    for browser in browsers:
        cat_cells = []
        cat_vals = []
        total_s, total_t = 0, 0
        for cat in all_categories:
            s = browser_cat_success[browser].get(cat, 0)
            t = browser_cat_total[browser].get(cat, 0)
            total_s += s
            total_t += t
            pct = (s / t * 100) if t > 0 else 0
            cat_cells.append(f"{pct:.0f}%")
            cat_vals.append(pct)
        total_pct = (total_s / total_t * 100) if total_t > 0 else 0
        row = [f"{total_pct:.0f}%"] + cat_cells
        row_vals = [total_pct] + cat_vals
        browser_rows.append((browser, row, row_vals, total_pct))

    browser_rows.sort(key=lambda x: x[3], reverse=True)
    sorted_browsers = [b for b, _, _, _ in browser_rows]
    rows = [r for _, r, _, _ in browser_rows]
    values = [v for _, _, v, _ in browser_rows]

    return sorted_browsers, col_labels, rows, values


def plot_category_table(results: dict[str, list[dict]], theme: Theme):
    browsers, col_labels, cells, values = build_category_table(results)
    if not browsers:
        return

    # Color thresholds
    if theme.name == "dark":
        color_good = "#4ADE80"  # green-400
        color_bad = "#F87171"  # red-400
    else:
        color_good = "#16A34A"  # green-600
        color_bad = "#DC2626"  # red-600

    # Write markdown table
    md_path = OUTPUT_DIR / f"category_breakdown_{theme.name}.md"
    header = "| Browser | " + " | ".join(col_labels) + " |"
    sep = "|---" * (len(col_labels) + 1) + "|"
    lines = [header, sep]
    for browser, row in zip(browsers, cells):
        lines.append("| " + browser + " | " + " | ".join(row) + " |")
    md_path.write_text("\n".join(lines) + "\n")

    # Render as matplotlib table figure
    n_rows = len(browsers)
    n_cols = len(col_labels)
    fig_width = max(10, 1.2 * n_cols)
    fig_height = max(2, 0.45 * (n_rows + 1))
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.axis("off")
    fig.set_facecolor(theme.background)

    table = ax.table(
        cellText=cells,
        rowLabels=browsers,
        colLabels=col_labels,
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.4)

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(theme.border)
        cell.set_linewidth(0.5)
        if row == 0:
            # Column headers
            cell.set_facecolor(theme.border)
            cell.set_text_props(color=theme.foreground, fontweight="bold")
        elif col == -1:
            # Row labels
            cell.set_facecolor(theme.border)
            cell.set_text_props(color=theme.foreground, fontweight="bold")
        else:
            # Data cells: green if >50%, red if <=50%
            cell.set_facecolor(theme.background)
            pct = values[row - 1][col]
            text_color = color_good if pct > 50 else color_bad
            cell.set_text_props(color=text_color)

    ax.text(
        0.5,
        0.98,
        "Success Rate by Vendor",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=14,
        color=theme.foreground,
    )
    fig.tight_layout()
    fig.savefig(
        OUTPUT_DIR / f"category_breakdown_{theme.name}.png",
        dpi=150,
        facecolor=theme.background,
        bbox_inches="tight",
    )
    plt.close(fig)


def plot_category_heatmap(results: dict[str, list[dict]], theme: Theme):
    """Heatmap with background-colored cells. Drops niche categories."""
    browser_cat_success: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    browser_cat_total: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for browser, runs in results.items():
        for run in runs:
            for cat, cnt in run.get("tasks_successful_by_category", {}).items():
                browser_cat_success[browser][cat] += cnt
            for cat, cnt in run.get("tasks_total_by_category", {}).items():
                browser_cat_total[browser][cat] += cnt

    cat_totals: dict[str, int] = defaultdict(int)
    for cats in browser_cat_total.values():
        for cat, cnt in cats.items():
            cat_totals[cat] += cnt
    categories = sorted(
        [c for c in cat_totals if c not in NICHE_CATEGORIES],
        key=lambda c: cat_totals[c],
        reverse=True,
    )

    browser_data = []
    for browser in results:
        total_s = sum(browser_cat_success[browser].get(c, 0) for c in categories)
        total_t = sum(browser_cat_total[browser].get(c, 0) for c in categories)
        total_pct = (total_s / total_t * 100) if total_t > 0 else 0
        row = [total_pct]
        for cat in categories:
            s = browser_cat_success[browser].get(cat, 0)
            t = browser_cat_total[browser].get(cat, 0)
            row.append((s / t * 100) if t > 0 else 0)
        browser_data.append((browser, row))

    browser_data.sort(key=lambda x: x[1][0], reverse=True)
    browsers = [b for b, _ in browser_data]
    matrix = np.array([r for _, r in browser_data])
    col_labels = ["Total"] + categories

    n_rows, n_cols = matrix.shape
    cell_w, cell_h = 1.0, 0.5
    label_w = 2.2
    footer_h = 1.0
    fig_w = label_w + n_cols * cell_w + 0.3
    fig_h = n_rows * cell_h + footer_h + 1.0

    grid_h = n_rows * cell_h
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_xlim(0, n_cols * cell_w)
    ax.set_ylim(-0.1, grid_h + footer_h)
    ax.invert_yaxis()
    ax.axis("off")
    fig.set_facecolor(theme.background)

    if theme.name == "dark":
        cmap = mcolors.LinearSegmentedColormap.from_list(
            "stealth", ["#7F1D1D", "#1A1A1A", "#14532D"], N=256
        )
        text_color = "#E5E5E5"
    else:
        cmap = mcolors.LinearSegmentedColormap.from_list(
            "stealth", ["#FEE2E2", "#FAFAFA", "#DCFCE7"], N=256
        )
        text_color = "#1A1A1A"

    for row_i in range(n_rows):
        y = row_i * cell_h
        for col_i in range(n_cols):
            val = matrix[row_i, col_i]
            color = cmap(val / 100.0)
            rect = mpatches.Rectangle(
                (col_i * cell_w, y),
                cell_w,
                cell_h,
                facecolor=color,
                edgecolor=theme.border,
                linewidth=0.5,
            )
            ax.add_patch(rect)
            fontweight = "bold" if col_i == 0 else "normal"
            ax.text(
                col_i * cell_w + cell_w / 2,
                y + cell_h / 2,
                f"{val:.0f}%",
                ha="center",
                va="center",
                fontsize=9,
                color=text_color,
                fontweight=fontweight,
            )

    for row_i, browser in enumerate(browsers):
        ax.text(
            -0.15,
            row_i * cell_h + cell_h / 2,
            browser,
            ha="right",
            va="center",
            fontsize=9,
            color=theme.foreground,
            fontweight="bold",
        )

    for col_i, label in enumerate(col_labels):
        fontweight = "bold" if col_i == 0 else "normal"
        ax.text(
            col_i * cell_w + cell_w / 2,
            grid_h + 0.15,
            label,
            ha="center",
            va="top",
            fontsize=9,
            color=theme.foreground,
            fontweight=fontweight,
            rotation=30,
            rotation_mode="anchor",
        )

    ax.set_title(
        "Success Rate by Vendor",
        fontsize=14,
        color=theme.foreground,
        pad=10,
    )

    fig.tight_layout()
    fig.savefig(
        OUTPUT_DIR / f"category_heatmap_{theme.name}.png",
        dpi=150,
        facecolor=theme.background,
        bbox_inches="tight",
    )
    plt.close(fig)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = load_results()

    if not results:
        print("No results found in official_results/")
        return

    print(
        f"Loaded {sum(len(v) for v in results.values())} runs across {len(results)} browsers"
    )

    for theme in [LIGHT, DARK]:
        plot_accuracy_by_browser(results, theme)
        plot_category_table(results, theme)
        plot_category_heatmap(results, theme)

    print(f"Saved plots to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
