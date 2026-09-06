#!/usr/bin/env python3
"""Verify one completed MMIO Metal run and summarize small-sample timings."""
import argparse
import json
import math
from pathlib import Path
import re
import statistics
import struct
import zlib
from driver_peer import DriverPeer

def distribution(values):
    if not values:raise ValueError('missing timing samples')
    ordered=sorted(values)
    return dict(n=len(values),min=min(values),p50=statistics.median(values),
        p95=ordered[math.ceil(.95*len(values))-1],p99=ordered[math.ceil(.99*len(values))-1],max=max(values),
        percentile_method='nearest rank; small sample tails are descriptive, not confidence bounds')

def verify(path):
    result=json.loads((path/'result.json').read_text());launch=json.loads((path/'launch.json').read_text())
    if not result.get('passed') or not result.get('driver_mmio') or result.get('debugger') or result.get('ram_restored'):
        raise ValueError('not a passed fresh MMIO Metal trial')
    argv=launch['argv'];env=launch['env']
    if any(flag in argv for flag in ('-gdb','-s','-S','-incoming','-loadvm')) or 'DARWIN_ANS_AUX_DRIVE' in env:
        raise ValueError('forbidden debugger, restored RAM, or namespace transport')
    drives=[argv[i+1] for i,v in enumerate(argv) if v=='-drive']
    if len(drives)!=1 or not drives[0].startswith('if=none,id=ans,'):raise ValueError('unexpected storage transport')
    original_shared=Path(env.get('DARWIN_GPU_SHM_PATH',''))
    if original_shared.name!='shared-ram.bin' or drives[0]!=f'if=none,id=ans,file={original_shared.parent}/disk.qcow2,format=qcow2':raise ValueError('shared RAM ownership')
    peer=DriverPeer.__new__(DriverPeer);peer.out=path
    peer.records=[json.loads(line) for line in (path/'driver-host.jsonl').read_text().splitlines()]
    verified=peer.verify(result['events'])
    raw=(path/'shared-ram.bin').read_bytes()
    session,seq,n,c=struct.unpack_from('<16sQII',raw,0x80)
    output=raw[0x800000:0x800000+n]
    if len(raw)!=0x1000000 or not 0<n<=0x200000 or session!=raw[16:32] or seq!=peer.records[-1]['seq'] or zlib.crc32(output)!=c or json.loads(output)!=peer.records[-1]['reply']:
        raise ValueError('final shared response does not match host record')
    if result.get('completion_source')=='shared-ram-audit':
        head,=struct.unpack_from('<Q',raw,0x180)
        if not 1<=head<=64:raise ValueError('final audit count')
        audit=[]
        for i in range(head):
            offset=0x1000+i*512;a_seq,a_n,a_crc=struct.unpack_from('<QII',raw,offset)
            data=raw[offset+16:offset+16+a_n]
            if a_seq!=i+1 or not 0<a_n<480 or zlib.crc32(data)!=a_crc:raise ValueError('final audit framing/CRC')
            audit.append(data.decode().strip())
        if audit!=[e['line'] for e in result['events'] if e.get('source')=='shared-ram-audit']:
            raise ValueError('shared audit differs from recorded guest results')
        if audit[-1]!='GPU_LOAD_COMPLETE result=pass scope=metal-driver-luma submissions=8 resources=0':
            raise ValueError('shared audit completion marker')
    runs=[];rpc=[]
    for e in result['events']:
        line=e['line']
        if 'GPU_LOAD_DRIVER_RUN ' in line:
            runs.append({k:int(v) for k,v in re.findall(r'(run|work_us|completed_us|cpu_reference_us)=(\d+)',line)})
        if 'GPU_LOAD_DRIVER_MMIO_RPC ' in line:
            fields=dict(re.findall(r'(seq|op|total_us|doorbell_us|polls)=([^ ]+)',line))
            rpc.append(dict(seq=int(fields['seq']),op=fields['op'],total_us=float(fields['total_us']),doorbell_us=float(fields['doorbell_us']),polls=int(fields['polls'])))
    if [r['seq'] for r in rpc]!=[r['seq'] for r in peer.records]:raise ValueError('guest/host RPC sequence disagreement')
    submits=[r for r in rpc if r['op']=='submit'];host=[r for r in peer.records if r['op']=='submit']
    if len(submits)!=8:raise ValueError('missing eight submissions')
    observation=result.get('native_observation') or result['driver'].get('readiness')
    if not observation or not observation.get('fresh_ack') or observation.get('stable_seconds')!=10:raise ValueError('missing native display/input observation')
    status=observation['input_status']
    if status.get('guest_state')!='R' or not status.get('presents'):raise ValueError('native display/input not ready')
    final_status=json.loads((path/'input-status.json').read_text())
    if final_status.get('guest_state')!='R' or final_status.get('presents',0)<status['presents']:raise ValueError('final native display/input not preserved')
    return dict(verification=verified,transport='owned shared RAM + MMIO doorbell + socket host completion; guest polls',
        native_observation=observation,guest_runs=runs,guest_rpc=rpc,
        timings_us=dict(work=distribution([r['work_us'] for r in runs]),
            cpu_reference=distribution([r['cpu_reference_us'] for r in runs]),
            submit_rpc=distribution([r['total_us'] for r in submits]),
            submit_doorbell=distribution([r['doorbell_us'] for r in submits]),
            host_service=distribution([r['host_service_us'] for r in host]),
            gpu=distribution([r['reply']['gpu_us'] for r in host])),
        requests=len(rpc),request_bytes=sum(r['request_bytes'] for r in peer.records),reply_bytes=sum(r['reply_bytes'] for r in peer.records),
        doorbells=len(rpc),completion_notifications=len(rpc),
        poll_iterations=sum(r['polls'] for r in rpc),
        limitations=['no interrupt delivery','poll iterations are not CPU-time accounting','not a zero-copy Metal resource path',
            'tiny compute workload only; no accelerated system presentation or Liquid Glass','no checkpoint/resume proof; QEMU migration blocked',
            '8 trials do not establish production p99 reliability'])

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('run',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();value=verify(a.run)
    with a.output.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    print(json.dumps(value['timings_us'],indent=2))
