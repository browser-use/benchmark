"""BU Bench v2.1 README chart from the website's source of truth.

uv run --no-project --with matplotlib python -I render.py DATA.json OUTDIR
One dot per model at its best-scoring effort. Asserts: label text == data, no label overlaps, nothing clipped.
"""
import json
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FixedLocator, NullLocator

for f in Path.home().joinpath('Library/Fonts').glob('GeistMono-*.otf'):
    font_manager.fontManager.addfont(str(f))
for f in Path.home().joinpath('Library/Fonts').glob('Geist-*.otf'):
    font_manager.fontManager.addfont(str(f))

THEMES = {
    'dark': dict(bg='#0A0A0A', fg='#F1F1F2', muted='#94949C', dim='#5E5E66', grid='#232326', ours='#A8E68B',
                 v={'OpenAI': '#F1F1F2', 'Anthropic': '#D9A27A', 'xAI': '#A1A1AA', 'DeepSeek': '#E58BD0',
                    'Z.ai': '#6FD6CB', 'Moonshot AI': '#B9A3F7', 'MiniMax': '#C9B458', 'Browser Use': '#A8E68B'}),
    'light': dict(bg='#FFFFFF', fg='#18181B', muted='#62626B', dim='#A1A1AA', grid='#E7E7EA', ours='#2E8B2E',
                  v={'OpenAI': '#18181B', 'Anthropic': '#A8683A', 'xAI': '#71717A', 'DeepSeek': '#B0358E',
                     'Z.ai': '#0F8F84', 'Moonshot AI': '#6A4BC4', 'MiniMax': '#7A6A12', 'Browser Use': '#2E8B2E'}),
}
SHORT = {'Browser Use (BU fast)': 'Browser Use agent', 'DeepSeek V4.1 Flash 1017': 'DeepSeek V4.1 Flash'}


def r1(v):
    return str(Decimal(str(v)).quantize(Decimal('.1'), rounding=ROUND_HALF_UP))


def rows(d):
    models = {m['slug']: m for m in d['models']}
    best = {}
    for c in d['configs']:
        if c['model'] not in best or c['score'] > best[c['model']]['score']:
            best[c['model']] = c
    # Keep only the newest DeepSeek checkpoint (older one is superseded by 1017).
    best.pop('deepseek-v4-1-flash', None)
    out = []
    for slug, c in best.items():
        m = models[slug]
        name = SHORT.get(m['name'], m['name'])
        eff = '' if m['ours'] or c['effort'] == 'default' else c['effort']
        out.append(dict(name=name, effort=eff, score=c['score'], cost=c['costPerTask'], vendor=m['vendor'],
                        ours=m['ours']))
    return sorted(out, key=lambda r: -r['score'])


def render(d, theme, out):
    t = THEMES[theme]
    plt.rcParams.update({'font.family': 'Geist Mono', 'font.size': 12, 'figure.facecolor': t['bg'],
                         'axes.facecolor': t['bg'], 'savefig.facecolor': t['bg'], 'text.color': t['fg'],
                         'axes.labelcolor': t['muted'], 'xtick.color': t['muted'], 'ytick.color': t['muted'],
                         'text.parse_math': False})
    fig = plt.figure(figsize=(16, 10), dpi=150)
    ax = fig.add_axes([.07, .1, .9, .72])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.grid(axis='both', color=t['grid'], lw=.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0, pad=10, labelsize=12)
    ax.set_xscale('log')
    ticks = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10]
    ax.xaxis.set_major_locator(FixedLocator(ticks))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_xticklabels([f'${x:g}' if x >= 1 else f'${x:.2f}' for x in ticks])
    ax.set_xlim(0.03, 16)
    ax.set_ylim(50, 86)
    ax.set_yticks(range(50, 90, 5))
    ax.set_yticklabels([f'{y}' for y in range(50, 90, 5)])
    ax.set_xlabel('Model cost per task (USD, log scale)', fontsize=13, labelpad=12)
    ax.set_ylabel('Mean rubric score / 100', fontsize=13, labelpad=12)

    x0 = ax.get_position().x0
    fig.text(x0, .925, 'Browser Use Benchmark v2.1', fontsize=30, weight='bold', family='Geist')
    fig.text(x0, .875, f"{d['tasks']} live-website tasks  ·  best effort per model  ·  updated {d['updated']}",
             fontsize=14, color=t['muted'])

    data = rows(d)
    data = [r for r in data if r['score'] >= 50]
    for r in data:
        c = t['v'][r['vendor']]
        ax.scatter(r['cost'], r['score'], s=260 if r['ours'] else 110, color=c, zorder=4,
                   edgecolors=t['bg'], linewidths=2)

    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    placed = []
    pts_px = [ax.transData.transform((r['cost'], r['score'])) for r in data]
    axbox = ax.get_window_extent(rend)
    cands = [(14, 0, 'left', 'center'), (-14, 0, 'right', 'center'), (0, 16, 'center', 'bottom'),
             (0, -16, 'center', 'top'), (12, 12, 'left', 'bottom'), (-12, 12, 'right', 'bottom'),
             (12, -12, 'left', 'top'), (-12, -12, 'right', 'top'), (30, 26, 'left', 'bottom'),
             (-30, 26, 'right', 'bottom'), (30, -26, 'left', 'top'), (-30, -26, 'right', 'top'),
             (50, 0, 'left', 'center'), (-50, 0, 'right', 'center'), (40, 44, 'left', 'bottom'),
             (-40, 44, 'right', 'bottom'), (40, -44, 'left', 'top'), (-40, -44, 'right', 'top')]
    order = sorted(range(len(data)), key=lambda i: (not data[i]['ours'], -data[i]['score']))
    texts = {}
    for i in order:
        r = data[i]
        label = f"{r['name']}{' ' + r['effort'] if r['effort'] else ''}  {r1(r['score'])}"
        c = t['ours'] if r['ours'] else t['v'][r['vendor']]
        ok = False
        for dx, dy, ha, va in cands:
            tx = ax.annotate(label, (r['cost'], r['score']), xytext=(dx, dy), textcoords='offset points',
                             ha=ha, va=va, fontsize=14 if r['ours'] else 12, color=c,
                             weight='bold' if r['ours'] else 'normal', zorder=5)
            bb = tx.get_window_extent(rend).expanded(1.04, 1.25)
            inside = bb.x0 >= axbox.x0 and bb.x1 <= axbox.x1 and bb.y0 >= axbox.y0 and bb.y1 <= axbox.y1
            hit = any(bb.overlaps(p) for p in placed)
            dot = any(bb.contains(px, py) or (bb.x0 - 6 < px < bb.x1 + 6 and bb.y0 - 6 < py < bb.y1 + 6)
                      for px, py in pts_px)
            if inside and not hit and not dot:
                far = abs(dx) > 20 or abs(dy) > 20 or (dx == 0 and abs(dy) >= 16)
                if far:
                    tx.remove()
                    tx = ax.annotate(label, (r['cost'], r['score']), xytext=(dx, dy), textcoords='offset points',
                                     ha=ha, va=va, fontsize=14 if r['ours'] else 12, color=c,
                                     weight='bold' if r['ours'] else 'normal', zorder=5,
                                     arrowprops=dict(arrowstyle='-', color=t['dim'], lw=.8, shrinkA=2, shrinkB=6))
                placed.append(bb)
                texts[i] = tx
                ok = True
                break
            tx.remove()
        assert ok, f'no label slot for {label}'
        assert label.endswith(r1(r['score']))
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return data


if __name__ == '__main__':
    d = json.load(open(sys.argv[1]))
    outdir = Path(sys.argv[2])
    outdir.mkdir(parents=True, exist_ok=True)
    for th in THEMES:
        data = render(d, th, outdir / f'bu_bench_v21_{th}.png')
    for r in data:
        print(f"{r['name']:24} {r['effort']:7} {r['score']:5} ${r['cost']:.3f}")
