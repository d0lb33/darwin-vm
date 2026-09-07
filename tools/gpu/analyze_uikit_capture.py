#!/usr/bin/env python3
"""Compare captured UIKit GPU output with its independent guest CPU reference.

Failed jobs can yield diagnostic images; this never promotes a failed process
or missing resource retirement into a passed consumer.
"""
import argparse
import base64
import hashlib
import json
import struct
import zlib
from pathlib import Path
from verify_audit_capture import verify_audits
from render_staging_capture import expand_render_staging


def write_png(path, bgra, width=320, height=480):
    """Lossless byte-format conversion only: no scaling, flip or retouching."""
    if len(bgra)!=width*height*4:raise ValueError('PNG extent')
    rgba=bytearray(bgra)
    rgba[0::4]=bgra[2::4];rgba[2::4]=bgra[0::4]
    rows=b''.join(b'\0'+rgba[y*width*4:(y+1)*width*4] for y in range(height))
    def chunk(kind, data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>2I5B',width,height,8,6,0,0,0))+
        chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b''))


def analyze(job):
    job=Path(job)
    audits=[json.loads(s) for s in (job/'driver-audit.jsonl').read_text().splitlines()]
    audit_mode=verify_audits(audits,(job/'shared-ram.bin').read_bytes())
    lines=[r['line'] for r in audits]
    records=[json.loads(s) for s in (job/'driver-host.jsonl').read_text().splitlines()]
    records=expand_render_staging(records)
    submits=[r for r in records if r['op']=='renderSubmit']
    if not submits or any(r['reply'].get('status')!=4 for r in submits):raise ValueError('no completed GPU rendering')
    targets={p['target'] for r in submits for p in r['request']['commands']}
    allocations={r['reply']['handle']:r['request'] for r in records if r['op']=='texture' and r['reply'].get('ok')}
    candidates={h for h,d in allocations.items() if (d.get('width'),d.get('height'),d.get('format'))==(320,480,80)}
    images={}
    for handle in candidates:
        reads=[r for r in records if r['op']=='read' and r['request'].get('texture')==handle]
        if not reads:continue
        image=bytearray()
        for r in reads:
            if not r['reply'].get('ok') or r['request'].get('offset',0)!=len(image):raise ValueError('read chunk order/result')
            raw=base64.b64decode(r['reply']['data'],validate=True)
            if len(raw)!=r['request'].get('length',320*480*4) or r['reply'].get('row')!=1280:raise ValueError('read chunk extent')
            if r['seq']<=submits[-1]['seq']:raise ValueError('read precedes GPU completion')
            image.extend(raw)
        if len(image)!=320*480*4:raise ValueError('incomplete image capture')
        images[handle]=bytes(image)
    gpu=[v for h,v in images.items() if h in targets];cpu=[v for h,v in images.items() if h not in targets]
    if len(gpu)!=1 or len(cpu)!=1:raise ValueError('requires GPU target and CPU reference captures')
    gpu,cpu=gpu[0],cpu[0]
    (job/'uikit-gpu.bgra').write_bytes(gpu);(job/'uikit-cpu.bgra').write_bytes(cpu)
    write_png(job/'uikit-gpu.png',gpu);write_png(job/'uikit-cpu.png',cpu)
    differences=[abs(a-b) for a,b in zip(gpu,cpu)]
    result=json.loads((job/'result.json').read_text())
    clean=not any(result[k] for k in ('spawn','exit','signal'))
    stats=[r for r in records if r['op']=='stats']
    retired=bool(stats and stats[-1]['reply'].get('live',{}).get('objects')==0)
    expected='GPU_LOAD_COMPLETE result=pass scope=quartzcore-render resources=0'
    pixels=all(d<=2 for d in differences)
    return dict(verified=clean and retired and expected in lines and pixels,
        scope='exact guest UIKit/CARenderer output vs guest CPU layer rendering; offscreen, not system compositor',
        audit_capture=audit_mode,gpu_passes=sum(r['reply']['passes'] for r in submits),
        gpu_draws=sum(r['reply']['draws'] for r in submits),pixels_match=pixels,
        differing_channels=sum(d>2 for d in differences),max_channel_error=max(differences),
        mean_channel_error=sum(differences)/len(differences),process_success=clean,resources_retired=retired,
        gpu_sha256=hashlib.sha256(gpu).hexdigest(),cpu_sha256=hashlib.sha256(cpu).hexdigest())


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('job',type=Path)
    a=p.parse_args();report=analyze(a.job)
    (a.job/'uikit-analysis.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
