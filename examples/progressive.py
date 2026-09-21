"""Observe exact Parquet row-group checkpoints, optionally stop at a coverage target.

Each printed row describes only its stated coverage. A cancellation request can
race with completion, so the final job state remains authoritative.
"""
import argparse,json
from session_client import RowTrail
p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');p.add_argument('--column',default='amount');p.add_argument('--rowtrail',default='rowtrail');p.add_argument('--workspace',default='.rowtrail');p.add_argument('--stop-after-files',type=int);p.add_argument('--stop-after-fragments',type=int);a=p.parse_args()
if a.stop_after_files is not None and a.stop_after_files<1:p.error('--stop-after-files must be positive')
if a.stop_after_fragments is not None and a.stop_after_fragments<1:p.error('--stop-after-fragments must be positive')
with RowTrail(a.rowtrail,a.workspace) as client:
 call=client.call;opened=call('open',{'source':a.source,'format':'parquet'});source={k:opened[k] for k in ('dataset_ref','manifest_ref')}
 request={'source':source,'aggregates':[{'function':'count','alias':'rows'},{'function':'count','column':a.column,'alias':'nonnull'},{'function':'sum','column':a.column,'alias':'total'},{'function':'avg','column':a.column,'alias':'mean'}],'execution':{'wait_ms':10,'preview':'available'}}
 result=call('analyze',request);job=result['job']['id'];last=None;cancelled=False
 while True:
  revision=result['readable_revision']
  if revision is not None and revision!=last:
   observed=call('read',{'result_ref':result['job']['result_ref'],'revision':revision});last=revision
   print(json.dumps({'checkpoint':observed},ensure_ascii=False),flush=True)
   coverage=observed['quality']['coverage']['input_coverage'];files=coverage['completed_files'];fragments=coverage['completed_fragments']
   enough=(a.stop_after_files is not None and files>=a.stop_after_files) or (a.stop_after_fragments is not None and fragments>=a.stop_after_fragments)
   if enough and not cancelled:
    call('control',{'action':'cancel','ref':job});cancelled=True
  if result['job']['state'] not in ('queued','running','stopping'):break
  result=call('control',{'action':'wait','ref':job,'wait_ms':10})
 print(json.dumps({'job':result['job'],'last_observed_revision':last},ensure_ascii=False),flush=True)
