#!/usr/bin/env python3
"""Independently verify a completed runner job's captured ring and GPU evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib

from consumer_verify import verify_records


def verify(path):
    p=Path(path);job=json.loads((p/'job.json').read_text());result=json.loads((p/'result.json').read_text())
    if result['spawn'] or result['exit'] or result['signal'] or result['pid']<=0:
        raise ValueError('guest process failed')
    digest=hashlib.sha256((p/'DVMProxy.bundle/DVMProxy').read_bytes()).hexdigest()
    if digest!=job['sha256']:raise ValueError('staged package changed')
    audits=[json.loads(x) for x in (p/'driver-audit.jsonl').read_text().splitlines()]
    raw=(p/'shared-ram.bin').read_bytes();head,=struct.unpack_from('<Q',raw,0x180)
    if not audits or len(audits)>120:raise ValueError('job audit extent')
    for i,a in enumerate(audits):
        seq=a['seq'];offset=0x1000+((seq-1)%120)*512
        actual,n,crc=struct.unpack_from('<QII',raw,offset)
        if seq>head or head-seq>=120 or (i and seq!=audits[i-1]['seq']+1) or seq!=actual or not 0<n<480:
            raise ValueError('audit sequence/retention')
        data=raw[offset+16:offset+16+n]
        if zlib.crc32(data)!=crc or data.decode().strip()!=a['line']:raise ValueError('audit content/CRC')
    lines=[a['line'] for a in audits]
    staged=[x for x in lines if x.startswith(f'GPU_LOAD_RUNNER_STAGED job={job["job"]} ')]
    if len(staged)!=1 or f'sha256={digest} ' not in staged[0]:raise ValueError('guest staging witness')
    end=lines.index('GPU_LOAD_COMPLETE result=pass scope=quartzcore-render resources=0')
    records=[json.loads(x) for x in (p/'driver-host.jsonl').read_text().splitlines()]
    for row in records:
        if 'upload_file' in row and hashlib.sha256((p/row['upload_file']).read_bytes()).hexdigest()!=row['upload_sha256']:
            raise ValueError('upload capture changed')
    evidence=verify_records(p,lines[:end+1],records,job.get('frames',1))
    return dict(job=job['job'],guest_pid=result['pid'],audit_crc_verified=True,consumer=evidence)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('job',type=Path)
    args=parser.parse_args();print(json.dumps(verify(args.job),indent=2))
