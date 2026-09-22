"""Render final alpha.7 exploration evidence; optional Matplotlib, no benchmark run."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

root = Path(__file__).resolve().parent
reports = [json.loads((root / f'performance/alpha7/exploration-{n}.json').read_text()) for n in (16384, 1048576)]
plt.rcParams.update({'font.family': 'DejaVu Sans', 'svg.fonttype': 'none', 'svg.hashsalt': 'rowtrail-alpha7', 'text.color': '#123c35', 'axes.labelcolor': '#53635e', 'xtick.color': '#53635e', 'ytick.color': '#123c35'})
fig, axes = plt.subplots(1, 2, figsize=(13.6, 6.2))
fig.set_facecolor('#fbf8f1')
fig.subplots_adjust(left=.21, right=.955, bottom=.30, top=.73, wspace=.22)
fig.text(.04, .91, 'Less overhead. The same durable trail.', fontsize=25, weight='bold')
fig.text(.04, .845, 'Open → save a subset → two saved-result branches → return to source', fontsize=13, color='#53635e')
labels = ['RowTrail alpha.6', 'RowTrail alpha.7', 'DuckDB 1.5.5', 'DataFusion 55.0.0']
colors = ['#b76836', '#147d70', '#80948b', '#80948b']
for i, (ax, report) in enumerate(zip(axes, reports)):
    s = report['summary_ms']
    values = [s['alpha6']['rowtrail'], s['alpha7']['rowtrail'], s['alpha7']['duckdb'], s['alpha7']['datafusion']]
    ax.set_facecolor('#fbf8f1')
    ax.barh(range(4), values, height=.5, color=colors, zorder=2)
    ax.set_yticks(range(4), labels if i == 0 else [''] * 4, fontsize=13)
    ax.set_ylim(3.7, -.7)
    ax.set_xlim(0, max(values) * 1.28)
    ax.tick_params(length=0, pad=9)
    ax.grid(axis='x', color='#dee5de', zorder=0)
    for spine in ax.spines.values(): spine.set_visible(False)
    for y, value in enumerate(values):
        ax.text(value + max(values) * .035, y, f'{value:.2f}', va='center', fontsize=12, weight='bold' if y == 1 else 'normal')
    ax.set_title(f"{report['rows']:,} rows", loc='left', fontsize=16, weight='bold', pad=16)
    ax.set_xlabel('Median ms · lower is better', fontsize=11, labelpad=12)
fig.text(.04, .16, '7 alternating runs/version · one Apple arm64 Mac · persistent sessions · warm OS cache', fontsize=11, color='#53635e')
fig.text(.04, .11, 'Direct engines retain in-memory tables; DataFusion startup excluded. RowTrail persists and verifies results.', fontsize=11, color='#53635e')
fig.text(.04, .06, 'Local backend timings, not a model-level efficiency claim. Raw data and limits: docs/verification.md', fontsize=11, color='#53635e')
fig.savefig(root / 'performance/alpha7/exploration.svg', facecolor=fig.get_facecolor(), metadata={'Date': None})
(root / 'local/alpha7').mkdir(parents=True, exist_ok=True)
fig.savefig(root / 'local/alpha7/exploration.png', facecolor=fig.get_facecolor(), dpi=120)
