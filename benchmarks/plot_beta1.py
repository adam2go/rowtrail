"""Render beta.1's retained local measurements; Matplotlib is a dev-only tool."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
data = ROOT / 'benchmarks/performance/beta1'
followup = json.loads((data / 'followup-2097152.json').read_text())['summary']
exploration = json.loads((data / 'exploration-1048576.json').read_text())['summary_ms']
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 12,
                     'svg.fonttype': 'none', 'svg.hashsalt': 'rowtrail-beta1'})
fig, axes = plt.subplots(3, 1, figsize=(9.5, 8.5), layout='constrained')
fig.patch.set_facecolor('#fafcfb')
panels = [
    ('Save subset + ten follow-ups + reconnect',
     [followup[v]['total_ms'] for v in ('alpha8', 'beta1')]),
    ('Ten follow-up queries on the saved result',
     [followup[v]['ten_queries_ms'] for v in ('alpha8', 'beta1')]),
    ('Separate five-query exploration · 1,048,576 rows',
     [exploration[v]['rowtrail'] for v in ('alpha8', 'beta1')]),
]
for axis, (title, values) in zip(axes, panels):
    axis.set_facecolor('#fafcfb')
    axis.barh(['alpha.8', 'beta.1'], values, color=['#8b9694', '#147d70'], height=.53)
    axis.invert_yaxis()
    axis.set_xlim(0, max(values) * 1.22)
    axis.set_title(title, loc='left', fontsize=14, fontweight='bold', pad=13, color='#163d37')
    axis.set_xlabel('Median elapsed time (ms) · lower is better', fontsize=10)
    axis.tick_params(axis='y', length=0, pad=9)
    axis.tick_params(axis='x', colors='#60716c', labelsize=10)
    axis.set_axisbelow(True)
    axis.grid(axis='x', color='#dce5e1', linewidth=.65)
    for spine in axis.spines.values():
        spine.set_visible(False)
    for i, value in enumerate(values):
        axis.text(value + max(values) * .02, i, f'{value:.1f}', va='center',
                  fontsize=13, fontweight='bold', color='#163d37')
fig.suptitle('Keep asking. Reuse the saved result.', fontsize=21, fontweight='bold',
             color='#163d37', ha='left', x=.02)
fig.supxlabel('One Apple arm64 Mac · 7 alternating trials per version · OS caches not flushed\n'
              'First two panels: 2,097,152 source rows; saved subset 28.16 MB.\n'
              'Save cost and warm-query tails rise. No model-level speed or token claim.',
              fontsize=9, color='#526b63')
svg = data / 'performance.svg'
fig.savefig(svg, metadata={'Date': None, 'Description':
    'RowTrail beta.1 local repeated measurements, including the separate exploration regression. '
    'Axes start at zero; panels use different scales. Conditions and raw data in verification.md.'})
svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines()) + '\n')
preview = ROOT / 'benchmarks/local/alpha9/performance.png'
preview.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(preview, dpi=150)
print(preview)
