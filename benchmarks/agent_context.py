"""Repeated transcript/token comparison with fair persistent DuckDB controls.

No model calls. Install requirements-context.txt in an isolated benchmark venv.
Tokenize actual JSON messages, separately from timing, using named encodings.
"""
import argparse, hashlib, importlib.metadata, json, os, pathlib, platform
import signal, statistics, sys, tempfile, time
import duckdb
import tiktoken

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'examples'))
from session_client import RowTrail
from agent_demo import fixture, SELECTED, TOTALS, FOLLOWUP


def text(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))
def literal(value):return "'"+str(value).replace("'","''")+"'"
def digest(path):
    with pathlib.Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def rowtrail_trial(binary, workspace, source, expected, mode):
    transcript=[];pid=None;jobs=[]
    class Client(RowTrail):
        def call(self,method,params,idempotency_key=None):
            value=super().call(method,params,idempotency_key)
            transcript.append({'request':self.last_request_text,'response':self.last_response_text})
            return value
    try:
        started=time.perf_counter()
        with Client(str(binary),str(workspace),response_mode=mode) as rt:
            opened=rt.open(source,label='orders',output={'max_rows':0,'max_bytes':2048})
            found=rt.inspect(opened,search='revenue',budget={'max_rows':5,'max_bytes':4096})
            assert [f['name'] for f in found['fields']]==['revenue_cents']
            rt.context(opened,columns=['order_id','channel','status','is_bot','revenue_cents'])
            selected=rt.query(SELECTED,{'source':opened},fetch=False,label='eligible orders',
                provenance={'description':'Paid orders excluding bots; amounts in integer cents.'})
            totals=rt.query(TOTALS,{'t':selected},label='channel revenue',
                provenance={'description':'Case-normalized channel revenue and order counts.'},
                execution={'output':{'max_rows':4,'max_bytes':8192}})
            assert rt.rows(totals)==expected
            checked=rt.check('SELECT order_id, COUNT(*) n FROM t GROUP BY order_id HAVING COUNT(*)<>1',
                             {'t':selected},samples=3,name='unique IDs')
            assert checked['passed'] and checked['violations']==0
            jobs=[selected['job']['id'],totals['job']['id'],checked['job']['id']]
        with Client(str(binary),str(workspace),response_mode=mode) as rt:
            found=rt.find('channel revenue')
            context=rt.context(found)
            assert context['context']['sql']==TOTALS
            answer=rt.query(FOLLOWUP,{'t':found})
            assert rt.typed_rows(answer)==[[sum(int(r[1]) for r in expected),sum(int(r[2]) for r in expected)]]
            jobs.append(answer['job']['id'])
            elapsed=(time.perf_counter()-started)*1000
            measured=list(transcript)
            # Diagnostics stay out of the measured/model transcript; retaining
            # them here proves saved follow-ups read zero original-source bytes.
            metrics=[rt.call('control',{'action':'status','ref':j})['job']['metrics'] for j in jobs]
            pid=rt.call('doctor',{})['coordinator_pid']
            assert all(m['io']['source_read_bytes']==0 for m in metrics[1:])
            card={'answer':rt.observe(totals),'check':{'passed':True,'violations':0,
                  'binding':checked['binding'],'quality':checked['quality']},'followup':rt.observe(answer)}
            return {'ms':elapsed,'calls':len(measured),'transcript':measured,'metrics':metrics,'decision_card':text(card)}
    finally:
        if pid:
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass


def duckdb_trial(path,source,expected,persist):
    transcript=[]
    started=time.perf_counter()
    con=duckdb.connect(str(path) if persist else ':memory:')
    con.execute('SET threads=1');con.execute("SET memory_limit='128MiB'")
    def call(sql,parameters=None,metadata=None):
        cursor=con.execute(sql,parameters or [])
        result=cursor.fetchall()
        rows=[[None if v is None else str(v) for v in row] for row in result]
        response=metadata if metadata is not None else {'columns':[d[0] for d in cursor.description],'rows':rows}
        transcript.append({'request':text({'sql':sql,'parameters':parameters or []}),'response':text(response)})
        return rows
    try:
        call('CREATE VIEW source AS SELECT * FROM read_parquet('+literal(source)+')',metadata={'table':'source'})
        fields=call("SELECT column_name,data_type FROM information_schema.columns WHERE table_name='source' AND lower(column_name) LIKE '%revenue%' ORDER BY ordinal_position")
        assert [r[0] for r in fields]==['revenue_cents']
        call("SELECT column_name,data_type FROM information_schema.columns WHERE table_name='source' AND column_name IN ('order_id','channel','status','is_bot','revenue_cents') ORDER BY ordinal_position")
        call('CREATE TABLE eligible AS '+SELECTED,metadata={'table':'eligible'})
        call('CREATE TABLE revenue AS '+TOTALS.replace('FROM t ','FROM eligible '),metadata={'table':'revenue'})
        totals=call('SELECT * FROM revenue ORDER BY channel');assert totals==expected
        violations=call('SELECT order_id,COUNT(*) n FROM eligible GROUP BY order_id HAVING COUNT(*)<>1')
        assert not violations
        if persist:
            con.close();con=duckdb.connect(str(path));con.execute('SET threads=1');con.execute("SET memory_limit='128MiB'")
        # SQL table discovery/schema are available without returning all rows.
        # Neither source history nor RowTrail quality is invented for a bare DB.
        call("SELECT table_name FROM information_schema.tables WHERE table_name='revenue'")
        call("SELECT column_name,data_type FROM information_schema.columns WHERE table_name='revenue' ORDER BY ordinal_position")
        answer=call(FOLLOWUP.replace('FROM t','FROM revenue'))
        assert answer==[[str(sum(int(r[1]) for r in expected)),str(sum(int(r[2]) for r in expected))]]
        elapsed=(time.perf_counter()-started)*1000
        return {'ms':elapsed,'calls':len(transcript),'transcript':transcript,
                'decision_card':text({'answer':totals,'check':{'violations':violations},'followup':answer}),
                'durability':'on-disk database, reopened connection' if persist else 'live connection and tables retained for handoff'}
    finally:con.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir',default='target/release');parser.add_argument('--rows',type=int,default=100000)
    parser.add_argument('--repeats',type=int,default=7);parser.add_argument('--output',default='benchmarks/local/beta3/agent-context.json')
    args=parser.parse_args();bins=(ROOT/args.bin_dir).resolve();records=[]
    encodings={name:tiktoken.get_encoding(name) for name in ('o200k_base','cl100k_base')}
    def count(raw):return {name:len(enc.encode(raw,disallowed_special=())) for name,enc in encodings.items()}
    variants=['rowtrail_full','rowtrail_compact','duckdb_memory','duckdb_file']
    with tempfile.TemporaryDirectory(prefix='rowtrail-context-bench-') as td:
        base=pathlib.Path(td);csv=base/'orders.csv';source=base/'orders.parquet'
        expected=fixture(csv,args.rows)
        with duckdb.connect() as con:
            con.execute('COPY (SELECT * FROM read_csv_auto('+literal(csv)+')) TO '+literal(source)+' (FORMAT PARQUET)')
        fixture_hash=digest(source);fixture_bytes=source.stat().st_size
        for repeat in range(args.repeats):
            order=variants[repeat%len(variants):]+variants[:repeat%len(variants)]
            if repeat%2:order=list(reversed(order))
            for name in order:
                target=base/f'{repeat}-{name}'
                if name.startswith('rowtrail_'):
                    trial=rowtrail_trial(bins/'rowtrail',target,source,expected,name.split('_')[1])
                else:trial=duckdb_trial(target,source,expected,name=='duckdb_file')
                trial.update({'repeat':repeat,'variant':name})
                trial['bytes']={key:sum(len(e[key].encode()) for e in trial['transcript']) for key in ('request','response')}
                trial['tokens']={key:{encoding:sum(count(e[key])[encoding] for e in trial['transcript']) for encoding in encodings} for key in ('request','response')}
                trial['decision_card_tokens']=count(trial['decision_card'])
                records.append(trial)
                print(name,repeat,round(trial['ms'],2),trial['tokens']['response'],flush=True)
        # Same data access, broader metadata: measure requesting the whole schema
        # separately. Never charge this avoidable dump to the database baseline.
        with RowTrail(str(bins/'rowtrail'),str(base/'schema')) as rt:
            pid=rt.call('doctor',{})['coordinator_pid']
            opened=rt.open(source,output={'max_rows':0})
            complete=rt.inspect(opened,budget={'max_rows':100,'max_bytes':16384});all_text=rt.last_response_text
            selected=rt.inspect(opened,search='revenue');selected_text=rt.last_response_text
            assert len(complete['fields'])==64 and len(selected['fields'])==1
        os.kill(pid,signal.SIGTERM)
        discovery={'full_fields':64,'search_fields':1,'full_tokens':count(all_text),'search_tokens':count(selected_text),
                   'full_response':all_text,'search_response':selected_text}
    summary={name:{'ms':statistics.median(r['ms'] for r in records if r['variant']==name),
        'calls':statistics.median(r['calls'] for r in records if r['variant']==name),
        'response_bytes':statistics.median(r['bytes']['response'] for r in records if r['variant']==name),
        'request_tokens':{e:statistics.median(r['tokens']['request'][e] for r in records if r['variant']==name) for e in encodings},
        'response_tokens':{e:statistics.median(r['tokens']['response'][e] for r in records if r['variant']==name) for e in encodings},
        'decision_card_tokens':{e:statistics.median(r['decision_card_tokens'][e] for r in records if r['variant']==name) for e in encodings}}
        for name in variants}
    report={'status':'passed','kind':__doc__,'platform':platform.platform(),'rows':args.rows,'columns':64,'repeats':args.repeats,
        'versions':{n:importlib.metadata.version(n) for n in ('duckdb','tiktoken')},
        'binary_sha256':{n:digest(bins/n) for n in ('rowtrail','rowtrail-runtime')},
        'fixture_sha256':fixture_hash,'fixture_bytes':fixture_bytes,'oracle':expected,
        'summary':summary,'schema_discovery':discovery,'records':records,
        'limitations':['No model calls. Named tokenizer counts exclude system prompts, tools/schema, reasoning, chat framing, caching and repeated conversation input.',
            'Per-message counts, not concatenated streams. Raw native JSON envelopes and a minimal DB SQL/row wrapper have different guarantees.',
            'Both engines project/aggregate before returning rows. Both retain tables; DuckDB gets live-memory and reopened durable-file controls.',
            'DB controls omit RowTrail lineage/quality/job/package contracts. They are not weaker at SQL or forced to discard work.',
            'RowTrail full/compact perform identical operations. Context discovery and checks are included; portable package export/import is separate in the offline demo.',
            'One machine; OS caches not flushed. Fresh workspaces/databases and alternating rotated order, serial runs without concurrent builds. Tokenization and diagnostics outside timing.',
            'Decision cards are code-composed output. Other engines can compose code too; this is not a RowTrail-exclusive saving.']}
    output=ROOT/args.output;output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
