"""Exact-guest shared IOSurface consumer evidence, separate from host tests."""
import hashlib
import math
import re
from pathlib import Path

WIDTH, HEIGHT, ROW = 1179, 2556, 4864
BYTES = ROW*HEIGHT


def fields(line):
    return dict(re.findall(r'(\w+)=([^ ]+)', line))


def verify_records(directory, lines, records, count):
    directory=Path(directory)
    if type(count) is not int or not 3<=count<=16:
        raise ValueError('shared consumer frame scope')
    if lines[-1]!='GPU_LOAD_COMPLETE result=pass scope=quartzcore-render resources=0':
        raise ValueError('shared consumer completion')
    if any(not r['reply'].get('ok') for r in records):
        raise ValueError('shared consumer backend error')
    ops=[r['op'] for r in records]
    if ops.count('sharedRenderCreate')!=1 or any(op in ('read','upload') for op in ops):
        raise ValueError('shared target creation or unexpected copied transfer')
    libraries=[r for r in records if r['op']=='library']
    if not libraries or any(r['request']['sha256']!='8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364' for r in libraries):
        raise ValueError('original guest AIR identity')
    target=next(r['reply']['handle'] for r in records if r['op']=='sharedRenderCreate')
    setup=[fields(x) for x in lines if x.startswith('GPU_LOAD_CA_SHARED_SETUP ')]
    if len(setup)!=1 or any(setup[0].get(k)!=str(v) for k,v in dict(width=WIDTH,height=HEIGHT,row=ROW,frames=count).items()):
        raise ValueError('shared setup geometry')
    if not math.isfinite(float(setup[0]['us'])) or float(setup[0]['us'])<0:
        raise ValueError('shared setup timing')
    transitions=[r for r in records if r['op'] in ('sharedRenderAcquire','sharedRenderSeal','sharedRenderRetire')]
    expected=[(op, epoch) for epoch in range(1,count+1) for op in ('sharedRenderAcquire','sharedRenderSeal','sharedRenderRetire')]
    if [(r['op'],r['request']['epoch']) for r in transitions]!=expected or any(r['request']['handle']!=target for r in transitions):
        raise ValueError('shared ownership/epoch sequence')
    frames=[fields(x) for x in lines if x.startswith('GPU_LOAD_CA_SHARED_FRAME ')]
    if [int(x['frame']) for x in frames]!=list(range(1,count+1)):
        raise ValueError('shared frame sequence')
    covered=set()
    for index, frame in enumerate(frames):
        acquire,seal,retire=transitions[index*3:index*3+3]
        renders=[r for r in records if r['op']=='renderSubmit' and acquire['seq']<r['seq']<seal['seq']]
        if not renders or any(r['reply'].get('status')!=4 for r in renders):
            raise ValueError('shared frame native GPU completion')
        covered.update(r['seq'] for r in renders)
        passes=[p for r in renders for p in r['request']['commands']]
        if not any(p.get('target')==target for p in passes) or sum(r['reply']['draws'] for r in renders)<1:
            raise ValueError('CARenderer did not draw into shared target')
        if seal['reply']['completedWrites']!=int(frame['completed_writes']) or seal['reply']['completedWrites']<1:
            raise ValueError('shared completed write ledger')
        request=retire['request']
        if request['swap']!=int(frame['swap']) or request['waitResult']!=0 or request['waitMode']!=1:
            raise ValueError('shared native retirement report')
        times=[float(frame[k]) for k in ('render_us','display_us','total_us')]
        if any(not math.isfinite(t) or t<0 for t in times) or abs(times[0]+times[1]-times[2])>.01:
            raise ValueError('shared timing fields')
    if covered!={r['seq'] for r in records if r['op']=='renderSubmit'}:
        raise ValueError('render submission outside acquired frame')
    stats=records[-1]
    if stats['op']!='stats' or stats['reply']['live']['objects'] or stats['reply']['live']['resourceBytes']:
        raise ValueError('shared resource retirement')
    final=[fields(x) for x in lines if x.startswith('GPU_LOAD_CA_SHARED_FINAL ')]
    if len(final)!=1 or final[0]['frames']!=str(count) or final[0]['bad_pixels']!='0' or final[0]['verification_reads_in_batch']!='0':
        raise ValueError('shared final pixel witness')
    pixels=(directory/'managed-final.bgra').read_bytes()
    if len(pixels)!=759*16384 or hashlib.sha256(pixels[:BYTES]).hexdigest()!=final[0]['sha']:
        raise ValueError('shared backing/guest hash mismatch')
    color=(0xffff0000,0xff00ff00,0xff0000ff)[(count-1)%3].to_bytes(4,'little')
    markers=b''.join(x.to_bytes(4,'little') for x in (0xff44564d,0xff505253,0xff424c52,0xff000000|count))
    for y in range(HEIGHT):
        wanted=(markers+color*(WIDTH-4)) if not y else color*WIDTH
        if pixels[y*ROW:y*ROW+WIDTH*4]!=wanted:
            raise ValueError(f'shared independent pixel oracle row {y}')
    log=(directory/'display.log').read_text(errors='replace')
    witnesses=re.findall(r'iomfb: gpu-present frame=(\d+) swap=(\d+) dva=(0x[0-9a-f]+) monotonic_ns=(\d+)',log)
    if [(int(w[0]),int(w[1])) for w in witnesses]!=[(int(f['frame']),int(f['swap'])) for f in frames]:
        raise ValueError('shared actual scanout frame/swap identity')
    last=log.rfind(f'iomfb: gpu-present frame={count} ')
    if 'D594 nested completed, status 0x0' not in log[last:] or 'GPU_LOAD_CA_SHARED_POWER_RESET rc=0' not in lines:
        raise ValueError('shared native completion/power reset')
    return dict(scope='exact-guest-CARenderer-owned-IOSurface-native-scanout-events',verified=True,frames=count,
                bytes=BYTES,sha256=final[0]['sha'],setup_us=float(setup[0]['us']),timings=frames,final_scanout_export_checked=False,
                caveat='compare final stopped-VM scanout export separately; display/input recovery is a separate check')


def verify_scanout_export(trial, job):
    actual=(Path(trial)/'last-presented.bgra').read_bytes()
    expected=(Path(job)/'managed-final.bgra').read_bytes()[:BYTES]
    if len(actual)!=BYTES or actual!=expected:
        raise ValueError('actual final DCP scanout differs from verified owned IOSurface')
    return dict(verified=True,bytes=BYTES,sha256=hashlib.sha256(actual).hexdigest(),
                scope='actual final DCP pixel DMA equals independently verified guest shared IOSurface')
