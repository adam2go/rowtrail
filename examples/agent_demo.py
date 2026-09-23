"""Offline agent-workflow demo. No model calls, downloads or dataframe dependencies.

rowtrail demo --directory ./rowtrail-demo
The two exploration runs differ only in response_mode. Full transcripts stay on
disk; bounded decision cards are the only analysis rows printed to the caller.
"""
import argparse
import csv
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time


SELECTED = ("SELECT order_id, lower(channel) channel, revenue_cents "
            "FROM source WHERE status = 'paid' AND is_bot = 0")
TOTALS = ("SELECT channel, COUNT(*) orders, SUM(revenue_cents) revenue_cents "
          "FROM t GROUP BY channel ORDER BY channel")
FOLLOWUP = 'SELECT SUM(orders) orders, SUM(revenue_cents) revenue_cents FROM t'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def client(binary, destination):
    # This is the trusted client shipped in the local executable, not package
    # contents or caller-authored provenance. It performs zero network requests.
    source = subprocess.check_output([binary, 'python-client'])
    path = destination / 'rowtrail_client.py'
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location('rowtrail_demo_client', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RowTrail


def fixture(path, rows):
    totals = {}
    with path.open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['order_id', 'channel', 'status', 'is_bot', 'revenue_cents'] +
                        [f'feature_{i:03}' for i in range(59)])
        for i in range(rows):
            channel = ('Organic', 'organic', 'Paid', 'Referral', 'Direct')[i % 5]
            status = 'cancelled' if i % 7 == 0 else 'paid'
            bot = int(i % 11 == 0)
            cents = 100 + (i * 17) % 10000
            writer.writerow([i, channel, status, bot, cents] + [(i + j) % 13 for j in range(59)])
            if status == 'paid' and not bot:
                item = totals.setdefault(channel.lower(), [0, 0])
                item[0] += 1
                item[1] += cents
    return [[name, str(count), str(cents)] for name, (count, cents) in sorted(totals.items())]


def run(binary, base, rows):
    RowTrail = client(binary, base)
    source = base / 'orders.csv'
    expected = fixture(source, rows)
    traces = {}
    elapsed = {}
    roots = {}
    selected_roots = {}
    cards = []

    class Metered(RowTrail):
        def call(self, method, params, idempotency_key=None):
            response = super().call(method, params, idempotency_key)
            self.trace.append({'request': json.loads(self.last_request_text),
                               'response': json.loads(self.last_response_text)})
            return response

    for mode in ('full', 'compact'):
        trace = []
        with Metered(binary, str(base / mode), response_mode=mode) as rt:
            rt.trace = trace
            start = time.perf_counter()
            opened = rt.open(source, label='raw orders', output={'max_rows':0,'max_bytes':2048})
            # The question names revenue; discover matching names without
            # dumping all 64 fields. This is metadata, not a data scan.
            fields = rt.inspect(opened, search='revenue', budget={'max_rows':5,'max_bytes':4096})
            assert [f['name'] for f in fields['fields']] == ['revenue_cents']
            rt.context(opened, columns=['order_id','channel','status','is_bot','revenue_cents'])
            selected = rt.query(SELECTED, {'source':opened}, label='eligible orders', fetch=False,
                provenance={'description':'Paid orders excluding bots; money is integer cents.'})
            assert not (selected.get('observation') or {}).get('rows')
            totals = rt.query(TOTALS, {'t':selected}, label='channel revenue',
                provenance={'description':'Revenue and order counts after case normalization and bot exclusion.'},
                execution={'output':{'max_rows':4,'max_bytes':8192}})
            assert rt.rows(totals) == expected
            roots[mode], selected_roots[mode] = rt.binding(totals), rt.binding(selected)
            elapsed[mode] = (time.perf_counter() - start) * 1000
            if mode == 'compact':
                cards.append({'step':'answer', **rt.observe(totals)})
        # Reconnect with only a workspace and label, as a fresh caller would.
        with Metered(binary, str(base / mode), response_mode=mode) as rt:
            rt.trace = trace
            start = time.perf_counter()
            found = rt.find('channel revenue')
            context = rt.context(found)
            assert context['context']['sql'] == TOTALS
            assert context['context']['inputs']['t'] == selected_roots[mode]
            followup = rt.query(FOLLOWUP, {'t':found})
            assert rt.typed_rows(followup) == [[sum(int(r[1]) for r in expected), sum(int(r[2]) for r in expected)]]
            elapsed[mode] += (time.perf_counter() - start) * 1000
            if mode == 'compact':
                cards.append({'step':'handoff context', 'binding':context['binding'],
                              'context':context['context'], 'verification':context['verification']})
        traces[mode] = trace
        (base / f'{mode}-transcript.json').write_text(json.dumps(trace, ensure_ascii=False, indent=2) + '\n')

    # These are RowTrail's reusable analysis contracts, beyond the shared SQL
    # calculation. Their work/calls are reported separately, not hidden in a
    # supposed engine-speed comparison.
    enhancements = []
    with Metered(binary, str(base/'compact'), response_mode='compact') as rt:
        rt.trace = enhancements
        checked = rt.check('SELECT order_id, COUNT(*) n FROM t GROUP BY order_id HAVING COUNT(*) <> 1',
                           {'t':selected_roots['compact']}, name='unique order IDs', samples=3)
        assert checked['passed'] and checked['violations'] == 0
        package = rt.pack(roots['compact'], base/'handoff',
                          notes='Paid orders, excluding bots. Channels normalized to lowercase. All amounts in cents.')
        cards.append({'step':'check', 'passed':checked['passed'], 'violations':checked['violations'],
                      'binding':checked['binding'], 'quality':checked['quality']})
    verified = RowTrail.verify_package(base/'handoff')
    assert verified['missing_inputs'] and not verified['sql_recomputed']
    recipe = {'format':'rowtrail.recipe.v1','inputs':['eligible_orders'],'steps':[
        {'id':'unique_ids','kind':'check','bindings':{'t':'input:eligible_orders'},
         'sql':'SELECT order_id,COUNT(*) n FROM t GROUP BY order_id HAVING COUNT(*)<>1'},
        {'id':'totals','bindings':{'t':'input:eligible_orders'},'sql':TOTALS}]}
    (base/'analysis-recipe.json').write_text(json.dumps(recipe,indent=2)+'\n')
    source.unlink()  # Only the fixture generated in this new private directory.
    # Import in a genuinely separate process, without the original CSV or SQL
    # execution. The receiver subsequently asks one explicit new question.
    receiver = '''import json,pathlib,sys
sys.path.insert(0,sys.argv[1])
from rowtrail_client import RowTrail
base=pathlib.Path(sys.argv[1])
with RowTrail(sys.argv[2],str(base/'receiver'),response_mode='compact') as rt:
    imported=rt.import_package(base/'handoff')
    # Explicit execution of the demo-authored recipe, after inert import. Only
    # the aggregate/check can be recomputed; the absent raw filtering input cannot.
    manifest=imported['provenance']
    root=next(n for n in manifest['nodes'] if n['id']==manifest['root'])
    eligible=imported['mapping'][root['dependencies']['t']]
    recipe=json.loads((base/'analysis-recipe.json').read_text())
    run=rt.run_recipe(recipe,{'eligible_orders':eligible},run_dir=base/'receiver-run')
    assert run['status']=='completed' and run['steps'][0]['passed']
    recomputed=rt.query('SELECT * FROM t ORDER BY channel',{'t':run['steps'][-1]['binding']})
    delivered=rt.query('SELECT * FROM t ORDER BY channel',{'t':imported})
    assert rt.rows(recomputed)==rt.rows(delivered)
    answer=rt.query('SELECT SUM(orders) orders,SUM(revenue_cents) revenue_cents FROM t',{'t':imported})
    result={'answer':rt.typed_rows(answer),'binding':rt.binding(answer),
            'verification':imported['verification'],'observation':rt.observe(answer),
            'explicit_recipe_status':run['status'],'included_aggregate_recomputed':True,
            'raw_filter_recomputed':False,'recipient_unique_ids':run['steps'][0]['passed']}
(base/'receiver.json').write_text(json.dumps(result,indent=2)+'\\n')
'''
    subprocess.run([sys.executable,'-c',receiver,str(base),binary],check=True)
    received = json.loads((base/'receiver.json').read_text())
    assert received['answer'] == [[sum(int(r[1]) for r in expected), sum(int(r[2]) for r in expected)]]
    assert received['included_aggregate_recomputed'] and received['recipient_unique_ids']
    cards.append({'step':'recipient follow-up without originals', **received['observation']})
    (base/'enhancement-transcript.json').write_text(json.dumps(enhancements,ensure_ascii=False,indent=2)+'\n')
    (base/'agent-cards.json').write_text(json.dumps(cards,ensure_ascii=False,indent=2)+'\n')

    def bytes_for(trace, key):
        return sum(len(encoded(event[key]).encode()) for event in trace)

    comparison = {mode:{'calls':len(trace), 'request_bytes':bytes_for(trace,'request'),
                        'response_bytes':bytes_for(trace,'response'),
                        'shared_workflow_ms':round(elapsed[mode],3)} for mode,trace in traces.items()}
    result = {'status':'passed','format':'rowtrail.agent-demo.v1', 'directory':str(base),
        'input_rows':rows,'input_columns':64,'qualifying_rows':sum(int(r[1]) for r in expected),
        'answer':{'columns':['channel','orders','revenue_cents'],'rows':expected},
        'transcripts':comparison,'producer_enhancement_calls':len(enhancements),
        'checks':{'independent_integer_oracle':True,'intermediate_rows_shown':0,
                  'schema_search':True,'reconnect_context':True,'unique_ids':checked['passed'],
                  'separate_recipient_process':True,'original_csv_removed':True,
                  'recipient_unique_ids':True,'included_aggregate_explicitly_recomputed':True,
                  'raw_filter_recomputed':False,
                  'package_verified':'bytes only; not authorship or SQL reproduction',
                  'sql_recomputed_on_import':False},
        'report':str(base/'report.md'),'package_report':str(base/'handoff/report.md'),
        'workspace':str(base/'compact'),'receiver':str(base/'receiver'),
        'limitations':['Scripted workflow; zero model calls. Transcript bytes are not billed model tokens.',
            'Both modes run identical SQL and keep large intermediates out of observations.',
            'Single demo timings include native startup, durability and handoff; not a speed benchmark.',
            'DuckDB can persist tables and return bounded answers too. RowTrail supplies explicit quality, fixed references, lineage and portable packages as shared contracts.',
            'Result-only package cannot recompute absent original inputs; import never runs saved SQL.']}
    (base/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    full, compact = (comparison[m]['response_bytes'] for m in ('full','compact'))
    report = f'''# A small answer, a reusable analysis

Verified {rows:,} synthetic orders with an independent integer oracle. The
64-column input was searched by name; no full intermediate table was displayed.

| Response mode | Native calls | UTF-8 response bytes |
|---|---:|---:|
| Full | {comparison['full']['calls']} | {full} |
| Compact | {comparison['compact']['calls']} | {compact} |

Both runs execute the same SQL with the same row/output budgets. These are complete
native response envelopes, not estimated model tokens. Requests and all responses
are retained in the two transcript files. The bounded `agent-cards.json` is the
suggested model-facing output of code composition; raw events can stay on disk.

## What survives a handoff

- A new connection finds `channel revenue`, its purpose, SQL and fixed inputs.
- A reusable uniqueness check has zero violations and a durable evidence binding.
- [The portable report](handoff/report.md) includes SQL, quality and bounded previews.
- A separate process imports checked result files and answers without the original
  CSV. Imported SQL is inert. Missing original inputs are declared explicitly.
- The recipient explicitly runs `analysis-recipe.json` on the included eligible
  orders, verifies uniqueness again, and reproduces the four delivered totals.
  `receiver-run/run.json` retains this execution. The initial filter cannot be
  recomputed without the original input; the report does not claim otherwise.

## Continue using the actual workspace

```python
from rowtrail_client import RowTrail
with RowTrail({binary!r}, {str(base/'compact')!r}, response_mode='compact') as rt:
    saved = rt.find('channel revenue')
    print(rt.context(saved))
    answer = rt.query('SELECT channel, revenue_cents FROM t ORDER BY revenue_cents DESC LIMIT 1', {{'t':saved}})
    print(rt.observe(answer))
```

The original CSV was deliberately removed, so no new computation against that
input is available. The receiver workspace contains independent snapshots.

## Comparison boundary

A direct database can also return four rows, persist tables and export Parquet.
RowTrail adds a common interface for versions, quality, discovery, checks and
handoff. It does not make SQL inherently faster or universally use fewer tokens
than a minimal SQL wrapper. This scripted demonstration is not an agent trial;
see the repository benchmarks for repeated timings and tokenizer measurements.
'''
    (base/'report.md').write_text(report)
    # A bounded summary only. Full schema, plans, metrics and row tables stay out
    # of the invoking agent's stdout; they remain inspectable in the directory.
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rowtrail',default='rowtrail')
    parser.add_argument('--directory')
    parser.add_argument('--rows',type=int,default=100000)
    args = parser.parse_args()
    if not 32 <= args.rows <= 1000000:
        parser.error('--rows must be 32..1000000')
    if args.directory:
        base = pathlib.Path(args.directory).absolute()
        base.mkdir(mode=0o700, parents=False, exist_ok=False)
    else:
        base = pathlib.Path(tempfile.mkdtemp(prefix='rowtrail-agent-demo-'))
    binary = str(pathlib.Path(args.rowtrail).absolute()) if os.path.sep in args.rowtrail else args.rowtrail
    try:
        run(binary,base,args.rows)
    except Exception as error:
        (base/'failure.json').write_text(json.dumps({'status':'failed','error':str(error)},indent=2)+'\n')
        raise


if __name__ == '__main__':
    main()
