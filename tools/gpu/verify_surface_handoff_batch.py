#!/usr/bin/env python3
"""Verify successive fresh guest revisions reused one retained owned surface."""
import argparse
import json
from pathlib import Path
from shared_consumer_verify import fields
from verify_runner_job import verify


def verify_batch(trial, jobs):
    trial=Path(trial)
    if len(jobs)<2 or len(set(jobs))!=len(jobs):
        raise ValueError('requires distinct successive jobs')
    results=[verify(trial/'runner-jobs'/str(j)) for j in jobs]
    handoffs=[r['handoff'] for r in results]
    if len({r['guest_pid'] for r in handoffs})!=len(jobs):
        raise ValueError('fresh process identities')
    if len({(r['surface_id'],r['registration_sha256']) for r in handoffs})!=1:
        raise ValueError('owned surface/page pool changed between processes')
    desc=[json.loads((trial/'runner-jobs'/str(j)/'job.json').read_text()) for j in jobs]
    if len({j['sha256'] for j in desc})<2:
        raise ValueError('requires distinct runtime driver revisions')
    audit=[json.loads(x) for x in (trial/'driver-audit.jsonl').read_text().splitlines()]
    owners=[fields(r['line']) for r in audit if r['line'].startswith('GPU_LOAD_SURFACE_OWNER ')]
    sends=[(r['seq'],fields(r['line'])) for r in audit if r['line'].startswith('GPU_LOAD_SURFACE_SEND ')]
    returns=[(r['seq'],fields(r['line'])) for r in audit if r['line'].startswith('GPU_LOAD_SURFACE_RETURN ')]
    if len(owners)!=1 or int(owners[0]['surface'])!=handoffs[0]['surface_id']:
        raise ValueError('single persistent supervisor owner')
    previous=0
    for job in jobs:
        start=[s for s,f in sends if int(f['job'])==job]
        end=[s for s,f in returns if int(f['job'])==job]
        if len(start)!=1 or len(end)!=1 or not previous<start[0]<end[0]:
            raise ValueError('overlapping or out-of-order handoffs')
        previous=end[0]
    return dict(verified=True,supervisor_pid=int(owners[0]['pid']),surface_id=handoffs[0]['surface_id'],
                registration_sha256=handoffs[0]['registration_sha256'],jobs=results,
                frames=sum(r['consumer']['frames'] for r in results),
                scope='sequential successful process handoff, exact guest rendering and native presentation; pacing distributions are per job, crash recovery is untested')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('trial',type=Path);p.add_argument('jobs',nargs='+',type=int)
    a=p.parse_args();print(json.dumps(verify_batch(a.trial,a.jobs),indent=2))
