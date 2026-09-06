#!/usr/bin/env python3
"""Classify the bounded MMIO-address failure or managed-address CPU control.

Neither result is accepted as zero-copy GPU proof. Verify audit CRC/sequence,
then the existing independent scanout/pixel evidence for the positive control.
"""
import argparse, json, re, struct, zlib
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('trial',type=Path)
p.add_argument('--kind',choices=('mmio-panic','managed-control'),required=True)
a=p.parse_args();r=json.loads((a.trial/'result.json').read_text())
b=(a.trial/'shared-ram.bin').read_bytes();head,=struct.unpack_from('<Q',b,0x180)
if len(b)!=0x1000000 or not 0<head<=120:raise ValueError('invalid audit extent')
lines=[]
for i in range(head):
    seq,n,crc=struct.unpack_from('<QII',b,0x1000+i*512)
    if seq!=i+1 or not 0<n<480:raise ValueError('invalid audit sequence/length')
    text=b[0x1010+i*512:0x1010+i*512+n]
    if zlib.crc32(text)!=crc:raise ValueError('audit CRC mismatch')
    lines.append(text.decode().strip())
if lines!=[e['line'] for e in r['events'] if e.get('source')=='shared-ram-audit']:
    raise ValueError('result does not match captured audit')
if a.kind=='mmio-panic':
    create=[x for x in lines if x.startswith('GPU_LOAD_SHARED_CREATE ')]
    if len(create)!=1 or not create[0].endswith('bytes=12432384 cache=0x700') or 'key=IOSurfaceAddress ' not in create[0]:
        raise ValueError('missing exact allocation request')
    panic=[e for e in r['events'] if 'physical page is before the start of DRAM: 0x13c0c0 < 0x4000000)' in e['line']]
    if r.get('passed') or len(panic)!=1 or any(x.startswith(('GPU_LOAD_SHARED_ALIAS ','GPU_LOAD_PRESENT_SETUP ','GPU_LOAD_PRESENT_FRAME ')) for x in lines):
        raise ValueError('not the bounded pre-GPU page-contract failure')
    result=dict(classification='disproven-within-scope',scope='existing MMIO RAM as caller-address IOSurface backing',
        request=create[0],panic=panic[0],requested_output_gpa=hex(0x13c0c0*16384),dram_base=hex(0x4000000*16384),
        zero_copy_gpu_proven=False)
else:
    evidence=json.loads((a.trial/'presentation-contract.json').read_text())
    pixels=json.loads((a.trial/'pixel-verification.json').read_text())
    aliases=[x for x in lines if x.startswith('GPU_LOAD_PRESENT_MANAGED_ALIAS verified=1 ')]
    releases=[x for x in lines if x=='GPU_LOAD_PRESENT_MANAGED_RELEASE verified=1 after_native_wait=1']
    if not r.get('passed') or not evidence.get('passed') or len(aliases)!=1 or len(releases)!=1 or pixels['bad_pixels'] or pixels['pixels']!=3013524:
        raise ValueError('managed allocation/display/release control incomplete')
    for frame in (1,2,3):
        wait=f'GPU_LOAD_PRESENT_RETURN frame={frame} op=wait rc=0x0 mode=1'
        if lines.count(wait)!=1 or lines.index(wait)>lines.index(releases[0]):raise ValueError('release before retirement')
    if [s['frame'] for s in evidence['scanouts']]!=[1,2,3]:raise ValueError('missing own scanouts')
    result=dict(classification='proven',scope='guest-managed caller backing aliases IOSurface; three CPU presentations and retirement/release',
        alias=aliases[0],release=releases[0],bad_pixels=0,pixels=3013524,rgb_sha256=pixels['actual_rgb_sha256'],
        host_shared_mapping_proven=False,zero_copy_gpu_proven=False,asynchronous_gpu_lifetime_proven=False)
(a.trial/'shared-surface-contract.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
