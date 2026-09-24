"""Render the frozen 60-task BU Bench V2 cost/quality comparison.

Run: uv run --group plots python generate_v2_plot.py
Only aggregate scores and recorded costs are needed; no credentials or task data.
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FixedLocator, FuncFormatter

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'official_results/bu_bench_v2_2026_09_23.json'
FONT = ROOT / 'fonts/GeistMono-Medium.otf'
if FONT.exists():
    font_manager.fontManager.addfont(str(FONT))
    plt.rcParams['font.family'] = 'Geist Mono'
plt.rcParams['svg.fonttype'] = 'none'
plt.rcParams['svg.hashsalt'] = 'bu-bench-v2-2026-09-23'


def price(value):
    return f'${value:.3f}' if value < 1 else f'${value:.2f}'


def render(theme):
    data = json.loads(DATA.read_text())
    models = data['models']
    assert len({m['name'] for m in models}) == len(models)
    assert all(m['n'] == 60 and 0 <= m['score'] <= 100 and m['cost_usd'] > 0 for m in models)
    dark = theme == 'dark'
    bg, fg, muted, grid = ('#0a0c10','#f1f4f8','#9ca9ba','#25303d') if dark else ('#fafbfd','#172331','#536377','#dbe2eb')
    colors = {'Xiaomi':'#ff983d' if dark else '#bd5800',
              'DeepSeek':'#6fd5bd' if dark else '#087b66',
              'OpenAI':'#9abaff' if dark else '#365ec0',
              'Anthropic':'#e0b091' if dark else '#935832',
              'Inception':'#e1a2e8' if dark else '#963ca1'}
    fig = plt.figure(figsize=(20,12),facecolor=bg)
    fig.text(.045,.949,'Browser Use Benchmark v2',fontsize=24,color=fg,weight='bold')
    fig.text(.045,.916,'Xiaomi MiMo added  |  Historical 60-task cut  |  September 23, 2026',fontsize=11,color=muted)
    ax = fig.add_axes([.058,.165,.535,.699],facecolor=bg)
    table = fig.add_axes([.635,.127,.335,.747],facecolor=bg)
    table.axis('off')
    ax.set_xscale('log');ax.set_xlim(.013,17);ax.set_ylim(0,100)
    ax.set_xlabel('Recorded agent cost per task (USD, log scale)',color=fg,labelpad=14,fontsize=11)
    ax.set_ylabel('Mean final findings score / 100',color=fg,labelpad=14,fontsize=11)
    ax.tick_params(colors=muted,labelsize=10,length=0,pad=8)
    ax.set_yticks(range(0,101,10))
    ax.xaxis.set_major_locator(FixedLocator([.02,.05,.1,.2,.5,1,2,5,10]))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x,_:f'${x:g}'))
    ax.grid(True,which='major',color=grid,linewidth=.65,zorder=0)
    ax.tick_params(which='minor',bottom=False)
    for s in ax.spines.values():s.set_visible(False)

    frontier=[];best=-1
    for m in sorted(models,key=lambda m:m['cost_usd']):
        if m['score']>best:frontier.append(m);best=m['score']
    ax.plot([m['cost_usd'] for m in frontier],[m['score'] for m in frontier],color=muted,alpha=.55,ls=(0,(4,5)),lw=1,zorder=1)

    # Text positions in data coordinates make the compact cluster readable.
    labels = {
        'GPT-6 Astra medium':(7.1,86,'right','GPT-6 Astra medium'),
        'GPT-6 Sol medium':(1.0,72.6,'left','GPT-6 Sol medium'),
        'GPT-6 Luna xhigh':(.29,62,'left','GPT-6 Luna xhigh'),
        'GPT-6 Luna high':(.105,68,'left','GPT-6 Luna high'),
        'GPT-6 Luna medium':(.055,58.5,'left','GPT-6 Luna medium'),
        'GPT-6 Luna low':(.016,46.7,'left','GPT-6 Luna low'),
        'DeepSeek V4.1 Flash max':(.40,53.1,'left','DeepSeek V4.1 Flash max'),
        'MiMo V2.6 Flash thinking':(.029,23.4,'left','MiMo V2.6 Flash'),
        'MiMo V2.6 Pro thinking':(.27,18.4,'left','MiMo V2.6 Pro'),
        'Mercury 2.5 (diffusion)':(.019,10.6,'left','Mercury 2.5 / diffusion'),
    }
    for m in models:
        x,y=m['cost_usd'],m['score'];c=colors.get(m['family'],muted)
        important=m['name'] in labels
        ax.scatter(x,y,s=150 if important else 88,c=c,edgecolors=bg,linewidth=1.2,zorder=4)
        ax.text(x,y,str(m['number']),ha='center',va='center',fontsize=6.6,color=bg,zorder=5,weight='bold')
        if m.get('added'):
            ax.scatter(x,y,s=240,facecolors='none',edgecolors=c,linewidth=1,zorder=3)
        if important:
            tx,ty,ha,label=labels[m['name']]
            ax.annotate(f"{label}\n{m['score']:.1f}  |  {price(x)}/task",xy=(x,y),xytext=(tx,ty),
                ha=ha,va='center',color=c,fontsize=9.0,linespacing=1.6,
                bbox=dict(boxstyle='round,pad=.28',fc=bg,ec='none',alpha=.96),
                arrowprops=dict(arrowstyle='-',color=c,alpha=.6,lw=.7),zorder=6)
    ax.text(.025,.97,'Higher + further left is better',transform=ax.transAxes,color=muted,fontsize=9)
    ax.text(.025,-.115,'Numbers match the table. Rings mark added models.',transform=ax.transAxes,color=muted,fontsize=9)

    table.text(0,1.015,'MODEL / REASONING',color=muted,fontsize=9,transform=table.transAxes)
    table.text(.77,1.015,'SCORE',color=muted,fontsize=9,ha='right',transform=table.transAxes)
    table.text(1,1.015,'$/TASK',color=muted,fontsize=9,ha='right',transform=table.transAxes)
    dy=1/len(models)
    for i,m in enumerate(models):
        y=1-(i+.60)*dy;c=colors.get(m['family'],muted)
        if m.get('added'):
            table.add_patch(plt.Rectangle((-.01,y-dy*.43),1.03,dy*.86,transform=table.transAxes,color=c,alpha=.10,lw=0))
        label=m['name'].replace('DeepSeek ','DS ').replace('Claude ','').replace(' thinking',' (think)').replace(' (diffusion)',' (diff.)')
        table.text(0,y,f"{m['number']:02d}  {label}",color=c,fontsize=8.3,va='center',transform=table.transAxes)
        table.text(.77,y,f"{m['score']:.2f}",color=fg,fontsize=8.3,ha='right',va='center',transform=table.transAxes)
        table.text(1,y,price(m['cost_usd']),color=fg,fontsize=8.3,ha='right',va='center',transform=table.transAxes)
    fig.text(.045,.061,'Final weighted rubric credit after judge penalties; not % of tasks solved. Historical API runs; no uncertainty intervals.',color=muted,fontsize=9)
    fig.text(.045,.041,'Original continuation/recovery replacements retained. Cost covers selected task results; judge, infrastructure and discarded attempts excluded.',color=muted,fontsize=8.7)
    fig.text(.045,.020,'Sources, pinned evaluation IDs and cohort notes: official_results/bu_bench_v2_2026_09_23.json  |  browser-use/benchmark',color=muted,fontsize=8.7)
    target=ROOT/'official_plots'/f'bu_bench_v2_mimo_{theme}'
    fig.savefig(target.with_suffix('.png'),dpi=200,facecolor=bg)
    fig.savefig(target.with_suffix('.svg'),facecolor=bg,metadata={'Date':'2026-09-23'})
    svg = target.with_suffix('.svg')
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines()) + '\n')
    plt.close(fig)
    print(target)


if __name__ == '__main__':
    for theme in ('dark','light'):
        render(theme)
