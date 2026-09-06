#!/usr/bin/env python3
"""Check bounded loader/mapping observations, never claim an MMIO data path."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def verify(path, management, require_display=False):
    result_path=path/'result.json'
    result=json.loads(result_path.read_text())
    lines=[e['line'] for e in result['events']]
    if result['stop_reason']!='guest load probe completed' or result.get('kept_paused'):
        raise ValueError('run did not complete and stop normally')
    if result.get('ram_restored') or result.get('debugger'):
        raise ValueError('requires a fresh debugger-free disk boot')
    marker='GPU_LOAD_COMPLETE result=recorded scope=mmio-loading-mapping-contracts'
    if sum(marker in line for line in lines)!=1:
        raise ValueError('missing unique contract completion')
    calls={}
    for line in lines:
        match=re.search(r'GPU_LOAD_CONTRACT_KEXT request=([\w-]+) kr=(0x[0-9a-f]+) op=(0x[0-9a-f]+) response_bytes=(\d+) log_bytes=(\d+)$',line)
        if match:
            key=match[1]
            if key in calls:raise ValueError('duplicate request result')
            calls[key]=dict(kr=int(match[2],16),op=int(match[3],16))
    expected={'daemon-ready':0xdc008004,'xml-no-predicate-control':0xdc008005,
              'mkext-recognition-gate':0x2e}
    if management!='none':
        expected['load-inert-codeless-personality']=0 if management=='accepted' else 0xdc008004
    if management=='accepted':expected['daemon-ready']=0xe00002d8
    if calls!={k:dict(kr=0,op=v) for k,v in expected.items()}:
        raise ValueError(f'loader results differ from this experiment: {calls}')
    witnesses=[
        'GPU_LOAD_CONTRACT_OPEN class=IOPlatformExpertDevice type=0 kr=0xe00002c2',
        'GPU_LOAD_CONTRACT_OPEN class=AppleNVMeNamespaceDevice type=0 kr=0x0',
        'GPU_LOAD_CONTRACT_MAP class=AppleNVMeNamespaceDevice memory_type=0 read_only=1 kr=0xe00002c2 bytes=0',
        'GPU_LOAD_CONTRACT_CLOSE kr=0x0',
    ]
    if management!='none':witnesses.append('GPU_LOAD_CONTRACT_OWN_MATCH kr=0x0 count=0')
    for witness in witnesses:
        if sum(witness in line for line in lines)!=1:raise ValueError('missing unique witness: '+witness)
    native=result.get('native_observation')
    if require_display and (not native or not native.get('fresh_ack') or native.get('stable_seconds')!=10 or native['input_status'].get('presents',0)<=0):
        raise ValueError('missing native display/input observation')
    return dict(scope='loading-and-mapping-prerequisites-only',transport_proven=False,
        data_transfers=0,gpu_submissions=0,management=management,calls=calls,
        own_service_immediate_count=0 if management!='none' else None,
        native_observation=native,source_result_sha256=hashlib.sha256(result_path.read_bytes()).hexdigest())


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('run',type=Path)
    p.add_argument('--management',required=True,choices=('none','rejected','accepted'))
    p.add_argument('--require-display',action='store_true')
    a=p.parse_args()
    print(json.dumps(verify(a.run,a.management,a.require_display),indent=2))
