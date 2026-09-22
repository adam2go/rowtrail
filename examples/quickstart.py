"""Explore, save a branch, and hand it to a fresh connection. No data download.

Python is only this example's host; RowTrail needs no language runtime or API key.
The new directory and its durable results are retained for further exploration.
"""
import argparse
from collections import defaultdict
import csv
import json
import pathlib
import tempfile

from session_client import RowTrail

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--rowtrail', default='rowtrail')
p.add_argument('--directory', help='A NEW directory; existing paths are never overwritten')
a = p.parse_args()
if a.directory:
    directory = pathlib.Path(a.directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
else:
    directory = pathlib.Path(tempfile.mkdtemp(prefix='rowtrail-demo-'))
source, workspace = directory/'orders.csv', directory/'workspace'
orders = []
with source.open('w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['id','region','amount_cents','status','channel'])
    for i in range(20003):
        row = [9007199254740993+i, ['east','west','north','south'][i%4],
               (i*29)%10001, 'refunded' if i%13 == 0 else 'paid', ['web','app','store'][i%3]]
        writer.writerow(row)
        orders.append(row)  # independent example oracle, never passed to RowTrail

with RowTrail(a.rowtrail, str(workspace)) as rt:
    opened = rt.open(source, label='demo orders')
    totals = rt.query("SELECT region,SUM(amount_cents) AS refunded_cents FROM orders WHERE status='refunded' GROUP BY region ORDER BY region", {'orders':opened})
    region = max(rt.rows(totals), key=lambda r:int(r[1]))[0]
    label = 'demo refunds: '+region
    saved = rt.query("SELECT id,amount_cents,channel FROM orders WHERE status='refunded' AND region=$1",
                     {'orders':opened}, parameters=[{'type':'Utf8','value':region}], label=label,
                     execution={'output':{'max_rows':0}})
    grouped = rt.query('SELECT channel,SUM(amount_cents) AS cents FROM saved GROUP BY channel ORDER BY channel', {'saved':saved})
    groups = rt.rows(grouped)
    assert grouped['job']['metrics']['io']['source_read_bytes'] == 0

# Only the workspace and descriptive label cross this connection boundary.
with RowTrail(a.rowtrail, str(workspace)) as rt:
    catalog = rt.call('workspace', {'action':'summary','kind':'result','label':label})
    assert catalog['next_cursor'] is None and len(catalog['items']) == 1
    found = catalog['items'][0]
    answer = rt.query('SELECT COUNT(*) AS n,MIN(id) AS first_id,SUM(amount_cents) AS cents FROM saved', {'saved':found})
    handoff = rt.rows(answer)
    io = answer['job']['metrics']['io']
    assert io['source_read_bytes'] == 0

expected_regions, expected_channels = defaultdict(int), defaultdict(int)
for _, key, cents, status, _ in orders:
    if status == 'refunded': expected_regions[key] += cents
assert rt.rows(totals) == [[key,str(expected_regions[key])] for key in sorted(expected_regions)]
subset = [r for r in orders if r[1] == region and r[3] == 'refunded']
for row in subset: expected_channels[row[4]] += row[2]
assert groups == [[key,str(expected_channels[key])] for key in sorted(expected_channels)]
assert handoff == [[str(len(subset)),str(min(r[0] for r in subset)),str(sum(r[2] for r in subset))]]
print(json.dumps({
    'status':'passed', 'workspace':str(workspace), 'input_rows':len(orders),
    'question':'Which region has the highest refunded value, and through which channels?',
    'refunds_by_region_cents':rt.rows(totals), 'chosen_region':region,
    'saved_label':label, 'saved_rows':found['row_count'], 'saved_binding':found['binding'],
    'channel_totals_cents':groups, 'handoff_columns':['n','first_id','cents'],
    'handoff_rows':handoff, 'handoff_calls':['workspace summary','query'],
    'handoff_original_source_bytes':io['source_read_bytes'],
    'checks':'Python integer oracle; exact/final/complete/untruncated guards; IDs above 2^53',
    'continue':'Use this workspace and label to query the retained result. Labels are descriptive, not unique IDs.'
}, ensure_ascii=False, indent=2))
