#!/usr/bin/env python3
"""Summarize a verified driver run without confusing GPU time with frame latency."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics


def distribution(values):
    if not values:
        return None
    return dict(count=len(values), min=min(values), median=statistics.median(values), max=max(values))


def analyze(run):
    verdict=json.loads((run/'result.json').read_text())
    if not verdict.get('passed') or verdict.get('driver',{}).get('submissions')!=8:
        raise ValueError('requires a verified eight-submission guest result')
    rows=[json.loads(s) for s in (run/'driver-host.jsonl').read_text().splitlines()]
    lines=[e['line'] for e in verdict['events'] if 'GPU_LOAD_DRIVER_RUN ' in e['line']]
    samples=[{k:int(v) for k,v in re.findall(r'(run|nonce|commit_us|completed_us|work_us|cpu_reference_us)=(\d+)',s)} for s in lines]
    if [s['run'] for s in samples]!=list(range(1,9)):
        raise ValueError('missing ordered guest samples')
    source={name:hashlib.sha256((run/name).read_bytes()).hexdigest()
        for name in ('result.json','driver-host.jsonl','driver-worker.log')}
    gpu=[r['reply']['gpu_us'] for r in rows if r['op']=='submit']
    paused=bool(list(run.glob('probe-snapshot-*')))
    report=dict(run=str(run.resolve()),source_sha256=source,diagnostic_pauses=paused,
        near_native_ui_proven=False,samples=samples,
        guest_us={k:distribution([s[k] for s in samples if k in s])
            for k in ('commit_us','completed_us','work_us','cpu_reference_us')},
        host_gpu_us=distribution(gpu),host_total_service_us=sum(r['host_service_us'] for r in rows),
        host_service_us={op:distribution([r['host_service_us'] for r in rows if r['op']==op])
            for op in sorted({r['op'] for r in rows})},
        scope='eight tiny two-pass reductions; not frame latency, glass fidelity or scrolling performance',
        cpu_reference_scope='input generation, half conversion and CPU sums; a conservative upper bound for CPU reduction alone')
    comparable=[s for s in samples if 'work_us' in s and 'cpu_reference_us' in s]
    if comparable:
        report['work_slower_than_cpu_reference_count']=sum(s['work_us']>s['cpu_reference_us'] for s in comparable)
        report['work_within_16_667_us_count']=sum(s['work_us']<=16667 for s in comparable)
    if paused:
        report['timing_limit']='diagnostic pauses invalidate this run as an uninterrupted latency trial'
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('run',type=Path);a=p.parse_args()
    text=json.dumps(analyze(a.run),indent=2)+'\n'
    (a.run/'driver-measurements.json').write_text(text)
    print(text,end='')
