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

def verify_records(out,lines,records,expected_frames=1):
    out=Path(out)
    if expected_frames!=1 and not 3<=expected_frames<=4096:raise ValueError('scene frame bound')
    sequence=expected_frames>1
    prefix='GPU_LOAD_CA_SEQUENCE_VERIFIED ' if sequence else 'GPU_LOAD_CA_VERIFIED '
    witnesses=[x for x in lines if x.startswith(prefix)]
    pattern=(r'GPU_LOAD_CA_SEQUENCE_VERIFIED width=64 height=64 frames=([0-9]+) passes=([1-9][0-9]*) draws=([1-9][0-9]*) bad_pixels=0'
             if sequence else r'GPU_LOAD_CA_VERIFIED width=64 height=64 passes=([1-9][0-9]*) draws=([1-9][0-9]*) bad_pixels=0')
    witness=re.fullmatch(pattern,witnesses[0]) if len(witnesses)==1 else None
    if not witness:raise ValueError('missing guest pixel oracle')
    values=tuple(map(int,witness.groups()))
    if sequence and (values[0]!=expected_frames or values[1]<expected_frames):raise ValueError('guest scene frame count')
    witnessed_counts=values[-2:]
    rejected=[r for r in records if not r['reply'].get('ok')]
    # QuartzCore may attempt optional pipeline prewarming after the verified
    # frame. An unused ENOTSUP creation does not invalidate a completed draw.
    if any(r['op']!='renderPipeline' or r['reply'].get('code')!=45 for r in rejected):raise ValueError('consumer host failure')
    libraries=[r for r in records if r['op']=='library']
    if len(libraries)!=1 or libraries[0]['request'].get('sha256')!=AIR_SHA:raise ValueError('consumer library identity')
    submits=[r for r in records if r['op']=='renderSubmit']
    if not submits or not sum(r['reply'].get('passes',0) for r in submits) or any(r['reply'].get('status')!=4 for r in submits):raise ValueError('missing actual render completion')
    counts=tuple(sum(r['reply'].get(key,0) for r in submits) for key in ('passes','draws'))
    if counts!=witnessed_counts:raise ValueError('guest/host render counts differ')
    targets={p['target'] for r in submits for p in r['request']['commands']}
    reads=[r for r in records if r['op']=='read' and r['request'].get('texture') in targets]
    if sequence:
        checkpoints=[0,1,expected_frames-1]
        if len(targets)!=1 or len(reads)!=3:raise ValueError('scene target/read count')
        markers=[f'GPU_LOAD_CA_SEQUENCE_PIXELS frame={f} bad_pixels=0' for f in checkpoints]
        if [x for x in lines if x.startswith('GPU_LOAD_CA_SEQUENCE_PIXELS ')]!=markers:raise ValueError('guest scene pixel witnesses')
        for frame,read in zip(checkpoints,reads):
            completed=sum(r['reply'].get('passes',0) for r in submits if r['seq']<read['seq'])
            if completed!=frame+1:raise ValueError('scene read/completion order')
            pixels=base64.b64decode(read['reply']['data'],validate=True)
            x0=frame*7%49
            row=b''.join(bytes([0,255,0,255]) if 28<=x<36 else bytes([0,0,255,255]) if x0<=x<x0+16 else bytes([255,0,0,255]) for x in range(64))
            if pixels!=row*64:raise ValueError('host target differs from moving layer oracle')
    else:
        if len(reads)!=1 or reads[0]['seq']<=max(r['seq'] for r in submits if r['reply'].get('passes')):raise ValueError('final-only target read')
        pixels=base64.b64decode(reads[0]['reply']['data'],validate=True)
        if pixels!=bytes([0,0,255,255])*4096:raise ValueError('host target differs from red layer oracle')
    final=records[-1]
    if final['op']!='stats' or final['reply']['live']['objects']!=0:raise ValueError('consumer resources not retired')
    (out/'consumer-final.bgra').write_bytes(pixels)
    result=dict(scope='exact-guest-CARenderer-64x64-moving-opaque-layers' if sequence else 'exact-guest-CARenderer-64x64-red-CALayer',frames=expected_frames,verified=True,bytes=len(pixels),sha256=hashlib.sha256(pixels).hexdigest(),render_passes=sum(r['reply'].get('passes',0) for r in submits),draws=sum(r['reply'].get('draws',0) for r in submits),air_sha256=AIR_SHA,live_resources=0,unsupported_pipeline_attempts=[dict(seq=r['seq'],description=r['reply'].get('description')) for r in rejected])
    (out/'consumer-verification.json').write_text(json.dumps(result,indent=2)+'\n');return result
