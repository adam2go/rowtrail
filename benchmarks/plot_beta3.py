"""Render beta.3's retained measurements, including the stronger SQL controls."""
import json,pathlib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=pathlib.Path(__file__).resolve().parents[1];data=ROOT/'benchmarks/performance/beta3'
report=json.loads((data/'agent-context.json').read_text());summary=report['summary'];schema=report['schema_discovery']
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':12,'svg.fonttype':'none'})
fig,axes=plt.subplots(1,3,figsize=(15,5.5));fig.patch.set_facecolor('#f7faf8')
sets=[(['Full','Compact'],[summary[v]['response_tokens']['o200k_base'] for v in ('rowtrail_full','rowtrail_compact')],
       'Same evidence, less metadata','Response tokens · o200k_base',lambda x:f'{x:,.0f}', ['#a6b5ae','#168578']),
      (['All 64 fields','Search: 1 field'],[schema[v]['o200k_base'] for v in ('full_tokens','search_tokens')],
       'Discover the relevant column','Schema-response tokens · o200k_base',lambda x:f'{x:,.0f}', ['#a6b5ae','#168578']),
      (['RowTrail\ncompact','DuckDB\nlive memory','DuckDB\ndurable file'],[summary[v]['ms'] for v in ('rowtrail_compact','duckdb_memory','duckdb_file')],
       'Direct SQL controls remain faster','Shared workflow · ms',lambda x:f'{x:.1f}', ['#168578','#d9a15a','#acbcb3'])]
for ax,(labels,values,title,ylabel,fmt,colors) in zip(axes,sets):
 ax.set_facecolor('#f7faf8');bars=ax.bar(labels,values,color=colors,width=.62,zorder=3)
 ax.set_title(title,fontsize=13,pad=22,fontweight='bold',color='#18362b');ax.set_ylabel(ylabel,fontsize=10)
 ax.set_ylim(0,max(values)*1.24);ax.spines[['top','right','left']].set_visible(False)
 ax.spines['bottom'].set_color('#c4cec8');ax.grid(axis='y',alpha=.15,zorder=0);ax.tick_params(axis='both',length=0,labelsize=10)
 for bar,value in zip(bars,values):ax.text(bar.get_x()+bar.get_width()/2,value+max(values)*.035,fmt(value),ha='center',fontsize=15,fontweight='bold',color='#18362b')
fig.suptitle('RowTrail beta.3  /  Spend context on evidence.',x=.06,ha='left',fontsize=22,fontweight='bold',color='#18362b')
fig.text(.06,.075,'100K rows · one Mac · seven rotated trials · medians. Schema search is a separate metadata probe.',fontsize=10,color='#4e6558')
fig.text(.06,.035,'No model calls or billed-token claims. DuckDB returns less metadata; both controls retain intermediate tables. Full raw transcripts are published.',fontsize=10,color='#4e6558')
fig.subplots_adjust(left=.06,right=.99,top=.73,bottom=.23,wspace=.4)
fig.savefig(data/'performance.svg',facecolor=fig.get_facecolor())
png=ROOT/'benchmarks/local/beta3/performance.png';fig.savefig(png,dpi=160,facecolor=fig.get_facecolor());print(png)
