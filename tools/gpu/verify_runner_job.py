#!/usr/bin/env python3
"""Independently verify a completed runner job's captured ring and GPU evidence."""
import argparse
import hashlib
import json
from pathlib import Path
from verify_audit_capture import verify_audits

from consumer_verify import verify_records


def verify(path):
    p=Path(path);job=json.loads((p/'job.json').read_text());result=json.loads((p/'result.json').read_text())
    if result['spawn'] or result['exit'] or result['signal'] or result['pid']<=0:
        raise ValueError('guest process failed')
    digest=hashlib.sha256((p/'DVMProxy.bundle/DVMProxy').read_bytes()).hexdigest()
    if digest!=job['sha256']:raise ValueError('staged package changed')
    audits=[json.loads(x) for x in (p/'driver-audit.jsonl').read_text().splitlines()]
    audit_mode=verify_audits(audits,(p/'shared-ram.bin').read_bytes())
    lines=[a['line'] for a in audits]
    staged=[x for x in lines if x.startswith(f'GPU_LOAD_RUNNER_STAGED job={job["job"]} ')]
    if len(staged)!=1 or f'sha256={digest} ' not in staged[0]:raise ValueError('guest staging witness')
    end=lines.index('GPU_LOAD_COMPLETE result=pass scope=quartzcore-render resources=0')
    records=[json.loads(x) for x in (p/'driver-host.jsonl').read_text().splitlines()]
    for row in records:
        if 'upload_file' in row and hashlib.sha256((p/row['upload_file']).read_bytes()).hexdigest()!=row['upload_sha256']:
            raise ValueError('upload capture changed')
    if job.get('shared_surface'):
        from shared_consumer_verify import verify_records as verify_shared
        evidence=verify_shared(p,lines[:end+1],records,job['frames'],job.get('hz',0))
    else:evidence=verify_records(p,lines[:end+1],records,job.get('frames',1),job.get('scene',0))
    output=dict(job=job['job'],guest_pid=result['pid'],audit_crc_verified=True,audit_capture=audit_mode,consumer=evidence)
    if job.get('surface_handoff'):
        from shared_consumer_verify import verify_handoff
        output['handoff']=verify_handoff(p,lines,job['job'],result['pid'])
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('job',type=Path)
    args=parser.parse_args();print(json.dumps(verify(args.job),indent=2))
