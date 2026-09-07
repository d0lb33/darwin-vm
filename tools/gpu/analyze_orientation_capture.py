#!/usr/bin/env python3
"""Verify exact-guest asymmetric image output against an analytic pixel array.

This is independent of the runner's older red-layer acceptance mode. It does
not promote a whole development session, UIKit, or system composition to pass.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import struct

from verify_audit_capture import verify_audits


def analyze(job):
    rows=[json.loads(s) for s in (job/'driver-host.jsonl').read_text().splitlines()]
    audits=[json.loads(s) for s in (job/'driver-audit.jsonl').read_text().splitlines()]
    mode=verify_audits(audits,(job/'shared-ram.bin').read_bytes())
    lines=[a['line'] for a in audits]
    result=json.loads((job/'result.json').read_text())
    if any(result[k] for k in ('spawn','exit','signal')):raise ValueError('guest process failed')
    for witness in ('GPU_LOAD_RENDERER_FLAGS value=2',
                    'GPU_LOAD_ORIENTATION_CONTENTS root=1 image=1',
                    'GPU_LOAD_ORIENTATION pixels=4096 differing_pixels=0',
                    'GPU_LOAD_COMPLETE result=pass scope=quartzcore-render resources=0',
                    'GPU_LOAD_DRIVER_AIR sha256=8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364 bytes=2705796'):
        if witness not in lines:raise ValueError('missing exact-guest witness: '+witness)
    submits=[r for r in rows if r['op']=='renderSubmit']
    if not submits or any(r['reply'].get('status')!=4 for r in submits):raise ValueError('GPU completion')
    if sum(r['reply']['draws'] for r in submits)!=1:raise ValueError('image draw count')
    targets={p['target'] for r in submits for p in r['request']['commands']}
    reads=[r for r in rows if r['op']=='read']
    if len(reads)!=2 or sum(r['request']['texture'] in targets for r in reads)!=1:
        raise ValueError('GPU and CPU captures required')
    colors=(0xffff0000,0xff00ff00,0xff0000ff,0xffffffff)
    expected=b''.join(struct.pack('<I',colors[((y-12)//16)*2+(x-8)//12]
        if 8<=x<32 and 12<=y<44 else 0) for y in range(64) for x in range(64))
    for r in reads:
        if r['seq']<=submits[-1]['seq'] or not r['reply'].get('ok') or r['reply'].get('row')!=256:
            raise ValueError('capture completion/extent')
        raw=base64.b64decode(r['reply']['data'],validate=True)
        if raw!=expected:raise ValueError('captured pixels differ from analytic nearest image')
    if rows[-1]['op']!='stats' or rows[-1]['reply']['live']['objects']!=0:
        raise ValueError('resources not retired')
    return dict(verified=True,scope='exact guest offscreen four-color CALayer, nearest sampling, renderer flags=2',
        audit_capture=mode,pixels=4096,sha256=hashlib.sha256(expected).hexdigest(),
        bundle_sha256=json.loads((job/'job.json').read_text())['sha256'],
        guest_pid=result['pid'],resources=0)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('job',type=Path)
    a=p.parse_args();result=analyze(a.job)
    (a.job/'orientation-analysis.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
