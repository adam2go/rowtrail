"""Plot the retained beta.2 release measurements; no hand-entered data."""
import json,pathlib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=pathlib.Path(__file__).resolve().parents[1];data=ROOT/'benchmarks/performance/beta2'
answer=json.loads((data/'answer-budget.json').read_text())['summary']
follow=json.loads((data/'followup.json').read_text())['summary']
explore=json.loads((data/'exploration-1m.json').read_text())['summary_ms']['beta2']
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':12,'svg.fonttype':'none'})
fig,axes=plt.subplots(1,3,figsize=(15,5.1));fig.patch.set_facecolor('#f7faf8')
sets=[(['beta.1\n2 calls','beta.2\n1 call'],[answer[v]['2048']['bytes'] for v in ('beta1','beta2')],
       '2 KiB scalar answer','Total response bytes',lambda x:f'{x:,.0f}'),
      (['beta.1','beta.2'],[follow[v]['total_ms'] for v in ('beta1','beta2')],
       '2M rows: save + 10 queries','Complete task, ms',lambda x:f'{x:.0f}'),
      (['RowTrail','DuckDB','DataFusion'],[explore[k] for k in ('rowtrail','duckdb','datafusion')],
       '1M rows: five-query exploration','Persistent sessions, ms',lambda x:f'{x:.1f}')]
for ax,(labels,values,title,ylabel,fmt) in zip(axes,sets):
 ax.set_facecolor('#f7faf8');colors=['#a6b5ae','#168578'] if len(values)==2 else ['#168578','#d9a15a','#acbcb3']
 bars=ax.bar(labels,values,color=colors,width=.62,zorder=3)
 ax.set_title(title,fontsize=14,pad=22,fontweight='bold',color='#18362b')
 ax.set_ylabel(ylabel,fontsize=11);ax.set_ylim(0,max(values)*1.27)
 ax.spines[['top','right','left']].set_visible(False);ax.spines['bottom'].set_color('#c4cec8')
 ax.grid(axis='y',alpha=.15,zorder=0);ax.tick_params(axis='both',length=0,labelsize=11)
 for bar,value in zip(bars,values):ax.text(bar.get_x()+bar.get_width()/2,value+max(values)*.035,fmt(value),ha='center',fontsize=15,fontweight='bold',color='#18362b')
fig.suptitle('RowTrail beta.2  /  Smaller answers. Reusable analysis.',x=.06,ha='left',fontsize=22,fontweight='bold',color='#18362b')
fig.text(.06,.045,'One Mac · seven alternating trials · medians · no external model calls. Direct engines retain in-memory intermediates; RowTrail persists them.',fontsize=10,color='#4e6558')
fig.subplots_adjust(left=.06,right=.99,top=.72,bottom=.2,wspace=.38)
fig.savefig(data/'performance.svg',facecolor=fig.get_facecolor())
png=ROOT/'benchmarks/local/beta2/performance.png';fig.savefig(png,dpi=160,facecolor=fig.get_facecolor())
print(png)
