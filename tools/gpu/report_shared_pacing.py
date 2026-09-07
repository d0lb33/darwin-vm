#!/usr/bin/env python3
"""Summarize independently verified shared rendering and its captured RPC cost."""
import argparse
import base64
import json
from pathlib import Path
from shared_consumer_verify import distribution
from verify_runner_job import verify


def report(directory):
    directory=Path(directory);evidence=verify(directory);consumer=evidence['consumer']
    records=[json.loads(x) for x in (directory/'driver-host.jsonl').read_text().splitlines()]
    acquired=[r for r in records if r['op']=='sharedRenderAcquire']
    retired=[r for r in records if r['op']=='sharedRenderRetire']
    samples={k:[] for k in ('host_service_us','gpu_us','rpc_count','upload_bytes','wire_request_bytes')}
    for first,last in zip(acquired[2:],retired[2:]):
        rows=[r for r in records if first['seq']<=r['seq']<=last['seq']]
        uploads=0
        for r in rows:
            if r['op']=='writeRenderBuffer':
                request=json.loads((directory/r['upload_file']).read_text())
                uploads+=len(base64.b64decode(request['data'],validate=True))
        values=dict(host_service_us=sum(r['host_service_us'] for r in rows),
                    gpu_us=sum(r['reply'].get('gpu_us',0) for r in rows),rpc_count=len(rows),upload_bytes=uploads,
                    wire_request_bytes=sum(r['request_bytes'] for r in rows))
        for key,value in values.items():samples[key].append(value)
    return dict(job=evidence['job'],guest_pid=evidence['guest_pid'],verified=True,
                audit_capture=evidence['audit_capture'],frames=consumer['frames'],setup_us=consumer['setup_us'],
                pacing=consumer.get('pacing'),memory_samples=consumer.get('memory_samples'),memory_change=consumer.get('memory_change'),
                service={k:distribution(v) for k,v in samples.items()},
                scope='individual job evidence; trial shutdown/recovery and final DCP export are separate checks')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('jobs',type=Path,nargs='+')
    a=p.parse_args();print(json.dumps([report(j) for j in a.jobs],indent=2))
