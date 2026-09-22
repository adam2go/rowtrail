"""Render the release comparison from retained measurements (Matplotlib only)."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
data=ROOT/'benchmarks/performance/alpha8'
auto=json.loads((data/'exploration-1048576.json').read_text())['summary_ms']
serial=json.loads((data/'exploration-serial-1048576.json').read_text())['summary_ms']
sort=json.loads((data/'resources.json').read_text())['summary']
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':12,'svg.fonttype':'none','svg.hashsalt':'rowtrail-alpha8'})
fig,axes=plt.subplots(2,1,figsize=(9,6.4),layout='constrained')
fig.patch.set_facecolor('#fafcfb')
palette=['#8b9694','#147d70']
panels=[('Complete five-query exploration · 1,048,576 rows',
         ['alpha.7 · 1 partition','alpha.8 · auto: 2'],
         [auto['alpha7']['rowtrail'],auto['alpha8']['rowtrail']],palette),
        ('Full sort · 1,048,576 rows · 32 MiB engine pool',
         ['alpha.7 · 1 partition','alpha.8 · 1 partition'],
         [sort['alpha7']['sort_wall_ms_including_observation'],sort['alpha8']['sort_wall_ms_including_observation']],['#8b9694','#147d70'])]
for axis,(title,labels,values,colors) in zip(axes,panels):
    axis.set_facecolor('#fafcfb')
    axis.barh(labels,values,color=colors,height=.53)
    axis.invert_yaxis()
    axis.set_xlim(0,max(values)*1.22)
    axis.set_title(title,loc='left',fontsize=14,fontweight='bold',pad=18,color='#163d37')
    axis.set_xlabel('Median elapsed time (ms) · lower is better',fontsize=11)
    axis.tick_params(axis='y',length=0,pad=9)
    axis.tick_params(axis='x',colors='#60716c',labelsize=10)
    axis.set_axisbelow(True)
    axis.grid(axis='x',color='#dce5e1',linewidth=.65)
    for spine in axis.spines.values():spine.set_visible(False)
    for i,value in enumerate(values):axis.text(value+max(values)*.02,i,f'{value:.1f}',va='center',fontsize=13,fontweight='bold',color='#163d37')
fig.suptitle('Keep results. Spend less time saving them.',fontsize=21,fontweight='bold',color='#163d37',ha='left',x=.02)
fig.supxlabel(f'One Apple arm64 Mac · 7 alternating exploration trials / 5 sort trials\nSeparate one-partition exploration: {serial["alpha7"]["rowtrail"]:.1f} → {serial["alpha8"]["rowtrail"]:.1f} ms.\nDirect engines remain faster; small pages from large results regress. Full conditions in the report.',fontsize=9,color='#526b63')
fig.savefig(data/'performance.svg',metadata={'Date':None,'Description':'RowTrail alpha.8 local repeated measurements; all timings start at zero. See verification.md for controls and tradeoffs.'})
svg=data/'performance.svg'
svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
preview=ROOT/'benchmarks/local/alpha8/performance.png';preview.parent.mkdir(parents=True,exist_ok=True)
fig.savefig(preview,dpi=150)
print(preview)
