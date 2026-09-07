"""Require exact-guest consumer audit, real host render completion and red pixels."""
import base64,hashlib,json,re,struct,zlib
from pathlib import Path
from surface_peer import AIR_SHA

def verify(out,events,records):
    out=Path(out);raw=(out/'shared-ram.bin').read_bytes();head,=struct.unpack_from('<Q',raw,0x180)
    if not 0<head<=120:raise ValueError('consumer audit bounds')
    lines=[]
    for i in range(head):
        seq,n,crc=struct.unpack_from('<QII',raw,0x1000+i*512);data=raw[0x1010+i*512:0x1010+i*512+n]
        if seq!=i+1 or not 0<n<480 or zlib.crc32(data)!=crc:raise ValueError('consumer audit integrity')
        lines.append(data.decode().strip())
    if lines!=[x['line'] for x in events if x.get('source')=='shared-ram-audit'] or lines[-1]!='GPU_LOAD_COMPLETE result=pass scope=quartzcore-render resources=0':raise ValueError('consumer completion')
    return verify_records(out,lines,records)

def verify_records(out,lines,records):
    out=Path(out)
    witnesses=[x for x in lines if x.startswith('GPU_LOAD_CA_VERIFIED ')]
    witness=re.fullmatch(r'GPU_LOAD_CA_VERIFIED width=64 height=64 passes=([1-9][0-9]*) draws=([1-9][0-9]*) bad_pixels=0',witnesses[0]) if len(witnesses)==1 else None
    if not witness:raise ValueError('missing guest pixel oracle')
    rejected=[r for r in records if not r['reply'].get('ok')]
    # QuartzCore may attempt optional pipeline prewarming after the verified
    # frame. An unused ENOTSUP creation does not invalidate a completed draw.
    if any(r['op']!='renderPipeline' or r['reply'].get('code')!=45 for r in rejected):raise ValueError('consumer host failure')
    libraries=[r for r in records if r['op']=='library']
    if len(libraries)!=1 or libraries[0]['request'].get('sha256')!=AIR_SHA:raise ValueError('consumer library identity')
    submits=[r for r in records if r['op']=='renderSubmit']
    if not submits or not sum(r['reply'].get('passes',0) for r in submits) or any(r['reply'].get('status')!=4 for r in submits):raise ValueError('missing actual render completion')
    counts=tuple(sum(r['reply'].get(key,0) for r in submits) for key in ('passes','draws'))
    if counts!=tuple(map(int,witness.groups())):raise ValueError('guest/host render counts differ')
    targets={p['target'] for r in submits for p in r['request']['commands']}
    reads=[r for r in records if r['op']=='read' and r['request'].get('texture') in targets]
    if len(reads)!=1 or reads[0]['seq']<=max(r['seq'] for r in submits if r['reply'].get('passes')):raise ValueError('final-only target read')
    pixels=base64.b64decode(reads[0]['reply']['data'],validate=True)
    if pixels!=bytes([0,0,255,255])*4096:raise ValueError('host target differs from red layer oracle')
    final=records[-1]
    if final['op']!='stats' or final['reply']['live']['objects']!=0:raise ValueError('consumer resources not retired')
    (out/'consumer-final.bgra').write_bytes(pixels)
    result=dict(scope='exact-guest-CARenderer-64x64-red-CALayer',verified=True,bytes=len(pixels),sha256=hashlib.sha256(pixels).hexdigest(),render_passes=sum(r['reply'].get('passes',0) for r in submits),draws=sum(r['reply'].get('draws',0) for r in submits),air_sha256=AIR_SHA,live_resources=0,unsupported_pipeline_attempts=[dict(seq=r['seq'],description=r['reply'].get('description')) for r in rejected])
    (out/'consumer-verification.json').write_text(json.dumps(result,indent=2)+'\n');return result
