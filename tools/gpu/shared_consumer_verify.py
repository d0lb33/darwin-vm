"""Exact-guest shared IOSurface consumer evidence, separate from host tests."""
import hashlib
import json
import math
import re
import struct
from pathlib import Path

WIDTH, HEIGHT, ROW = 1179, 2556, 4864
BYTES = ROW*HEIGHT


def fields(line):
    return dict(re.findall(r'(\w+)=([^ ]+)', line))


def verify_records(directory, lines, records, count, hz=0, scene=None):
    directory=Path(directory)
    # A running supervisor imports this verifier on its first completed job.
    # Retain the earlier call signature: derive a missing scene from the
    # immutable staged job, never from the guest's claimed setup line.
    if scene is None:
        job=directory/'job.json'
        scene=json.loads(job.read_text()).get('scene',0) if job.exists() else 0
    if type(count) is not int or not 3<=count<=1024 or hz not in (0,30,60) or scene not in range(4):
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
    if int(setup[0].get('scene',0))!=scene:raise ValueError('shared requested scene contract')
    measured='hz' in setup[0]
    if (hz or count>16) and not measured:
        raise ValueError('missing shared pacing instrumentation')
    if measured and (setup[0]['hz']!=str(hz) or setup[0].get('warmup')!='2'):
        raise ValueError('shared requested pacing contract')
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
    verify_pixels(pixels,count,scene)
    log=(directory/'display.log').read_text(errors='replace')
    witnesses=re.findall(r'iomfb: gpu-present frame=(\d+) swap=(\d+) dva=(0x[0-9a-f]+) monotonic_ns=(\d+)',log)
    if [(int(w[0]),int(w[1])) for w in witnesses]!=[(int(f['frame']),int(f['swap'])) for f in frames]:
        raise ValueError('shared actual scanout frame/swap identity')
    last=log.rfind(f'iomfb: gpu-present frame={count} ')
    if 'D594 nested completed, status 0x0' not in log[last:] or 'GPU_LOAD_CA_SHARED_POWER_RESET rc=0' not in lines:
        raise ValueError('shared native completion/power reset')
    result=dict(scope='exact-guest-CARenderer-owned-IOSurface-native-scanout-events',verified=True,frames=count,scene=scene,
                bytes=BYTES,sha256=final[0]['sha'],setup_us=float(setup[0]['us']),timings=frames,final_scanout_export_checked=False,
                caveat='compare final stopped-VM scanout export separately; display/input recovery is a separate check')
    if measured:
        result['pacing']=verify_pacing(frames,hz,[int(w[3])/1000 for w in witnesses])
        if setup[0].get('profile')=='1':
            names=('acquire_us','update_us','encode_us','seal_us','enqueue_us','wait_us','retire_us')
            for f in frames:
                values=[float(f[k]) for k in names]
                if any(not math.isfinite(v) or v<0 for v in values) or abs(sum(values[:4])-float(f['render_us']))>.02 or abs(sum(values[4:])-float(f['display_us']))>.02:
                    raise ValueError('shared stage profile accounting')
            result['pacing']['stage_us']={k:distribution([float(f[k]) for f in frames[2:]]) for k in names}
        memory=[fields(x) for x in lines if x.startswith('GPU_LOAD_CA_SHARED_MEMORY ')]
        wanted=sorted({2,count,*range(128,count+1,128)})
        if [int(m['frame']) for m in memory]!=wanted:raise ValueError('shared memory sample coverage')
        for sample in memory:
            frame=int(sample['frame']);low=transitions[3*frame-1]['seq']
            high=transitions[3*frame]['seq'] if frame<count else math.inf
            snapshots=[r['reply']['live'] for r in records if r['op']=='stats' and low<r['seq']<high]
            if not snapshots or int(sample['objects'])!=snapshots[0]['objects'] or int(sample['resource_bytes'])!=snapshots[0]['resourceBytes']:
                raise ValueError('shared resource sample differs from backend')
            if min(int(sample[k]) for k in ('resident','footprint','objects','resource_bytes'))<=0:
                raise ValueError('shared memory sample values')
        result['memory_samples']=memory
        result['memory_change']={k:int(memory[-1][k])-int(memory[0][k]) for k in ('resident','footprint','objects','resource_bytes')}
    return result


def verify_pixels(pixels, frame, scene):
    if len(pixels)<BYTES or scene not in range(4):raise ValueError('shared pixel extent/scene')
    colors=[(0xffff0000,0xff00ff00,0xff0000ff)[(frame-1)%3]]*WIDTH
    normalize=bytearray(range(256));normalize[127]=128;normalize[129]=128
    if scene:
        colors=[0xff0000ff]*WIDTH;left=(frame*17)%(WIDTH-159)
        for x in range(left,left+160):
            if scene==2 and not 80<=x<WIDTH-80:continue
            colors[x]=0xff800080 if scene==1 else 0xff00ff00 if scene==3 and x>=left+80 else 0xffff0000
        colors[280:360]=[0xff00ff00]*80
    row=b''.join(c.to_bytes(4,'little') for c in colors)
    markers=b''.join(x.to_bytes(4,'little') for x in (0xff44564d,0xff505253,0xff424c52,0xff000000|frame))
    for y in range(HEIGHT):
        actual=pixels[y*ROW:y*ROW+WIDTH*4];wanted=markers+row[16:] if y==0 else row
        if scene==1:
            # One RGB code tolerance only in the expected half-opacity region.
            # Marker bytes, alpha and all other colors remain exact.
            adjusted=bytearray(actual)
            for x,c in enumerate(colors):
                if c==0xff800080 and (y or x>=4):
                    for channel in (0,2):adjusted[x*4+channel]=normalize[adjusted[x*4+channel]]
            actual=bytes(adjusted)
        if actual!=wanted:raise ValueError(f'shared independent pixel oracle row {y}')


def distribution(values):
    values=sorted(values)
    if not values:return dict(samples=0)
    n=len(values)
    return dict(samples=n,min=min(values),mean=sum(values)/n,p50=values[math.ceil(n*.5)-1],
                p95=values[math.ceil(n*.95)-1],p99=values[math.ceil(n*.99)-1],max=max(values))


def verify_pacing(frames,hz,scanout_us):
    if len(scanout_us)!=len(frames) or any(b<=a for a,b in zip(scanout_us,scanout_us[1:])):
        raise ValueError('nonmonotonic native scanout timestamps')
    starts=[];finishes=[];targets=[]
    previous=0
    for i,frame in enumerate(frames):
        start,finish,target=[float(frame[k]) for k in ('start_us','finish_us','target_us')]
        if any(not math.isfinite(x) for x in (start,finish,target)) or start<previous-.01 or finish<start or abs(finish-start-float(frame['total_us']))>.02:
            raise ValueError('shared frame clock interval')
        if hz and i>=2:
            expected=float(frames[2]['target_us'])+(i-2)*1e6/hz
            if target<0 or abs(target-expected)>.02 or start+.02<target:
                raise ValueError('shared absolute pacing targets')
        elif target!=-1:raise ValueError('unexpected shared pacing target')
        starts.append(start);finishes.append(finish);targets.append(target);previous=finish
    steady=frames[2:];late=[max(0,f-t-1e6/hz) for f,t in zip(finishes[2:],targets[2:])] if hz else []
    elapsed=finishes[-1]-starts[2]
    return dict(hz=hz,warmup_frames=2,measured_frames=len(steady),first_use_us=[float(f['total_us']) for f in frames[:2]],
                work_us=distribution([float(f['total_us']) for f in steady]),
                render_us=distribution([float(f['render_us']) for f in steady]),
                display_us=distribution([float(f['display_us']) for f in steady]),
                elapsed_us=elapsed,completed_frames_per_second=len(steady)*1e6/elapsed if elapsed else None,
                over_60hz_work_budget=sum(float(f['total_us'])>1e6/60 for f in steady),
                deadline_misses=sum(x>0 for x in late),deadline_lateness_us=distribution(late),
                start_lateness_us=distribution([max(0,s-t) for s,t in zip(starts[2:],targets[2:])]) if hz else dict(samples=0),
                native_scanout_frames_per_second=(len(steady)-1)*1e6/(scanout_us[-1]-scanout_us[2]) if len(steady)>1 else None,
                scanout_intervals_us=distribution([b-a for a,b in zip(scanout_us[2:],scanout_us[3:])]),
                caveat='guest work excludes post-frame audit/memory sampling; absolute schedule and native intervals include its effect; every frame rendered, no target skipping')


def verify_scanout_export(trial, job):
    actual=(Path(trial)/'last-presented.bgra').read_bytes()
    expected=(Path(job)/'managed-final.bgra').read_bytes()[:BYTES]
    if len(actual)!=BYTES or actual!=expected:
        raise ValueError('actual final DCP scanout differs from verified owned IOSurface')
    return dict(verified=True,bytes=BYTES,sha256=hashlib.sha256(actual).hexdigest(),
                scope='actual final DCP pixel DMA equals independently verified guest shared IOSurface')


def verify_handoff(directory, lines, job, pid):
    """Additional proof, required alongside rendering/pixels/native retirement."""
    events={}
    for tag in ('SEND','RECEIVED','RETURN'):
        rows=[fields(x) for x in lines if x.startswith('GPU_LOAD_SURFACE_'+tag+' ')]
        if len(rows)!=1 or rows[0]['job']!=str(job):
            raise ValueError('surface handoff job witness '+tag)
        events[tag]=rows[0]
    surface=int(events['SEND']['surface'])
    if not surface or events['SEND']['result']!='0' or any(int(x['surface'])!=surface for x in events.values()):
        raise ValueError('surface handoff identity')
    if events['RETURN']['alias_verified']!='1' or events['RETURN']['pid']!=str(pid):
        raise ValueError('supervisor did not verify returned child alias')
    if events['RECEIVED']['bytes']!=str(759*16384):
        raise ValueError('surface handoff extent')
    positions=[next(i for i,x in enumerate(lines) if x.startswith('GPU_LOAD_SURFACE_'+tag+' ')) for tag in ('SEND','RECEIVED','RETURN')]
    done=lines.index('GPU_LOAD_COMPLETE result=pass scope=quartzcore-render resources=0')
    setup=[fields(x) for x in lines if x.startswith('GPU_LOAD_CA_SHARED_SETUP ')]
    if not positions[0]<positions[1]<done<positions[2] or len(setup)!=1 or int(setup[0]['surface'])!=surface:
        raise ValueError('surface handoff rendering/return order or target')
    pixels=(Path(directory)/'managed-final.bgra').read_bytes()
    if struct.unpack_from('<QQ',pixels,BYTES)!=(job,job^0x44564d48414e4446):
        raise ValueError('surface handoff independent shared-memory witness')
    registration=(Path(directory)/'managed-pages.bin').read_bytes()
    if len(registration)!=32+759*8:
        raise ValueError('surface handoff page registration extent')
    session=(Path(directory)/'shared-ram.bin').read_bytes()[16:32]
    pages=struct.unpack_from('<759Q',registration,32)
    if registration[:16]!=session or struct.unpack_from('<QQ',registration,16)!=(759*16384,759) or len(set(pages))!=759 or any(x%16384 or x>0x300000000-16384 for x in pages):
        raise ValueError('surface handoff page registration contract')
    return dict(verified=True,surface_id=surface,guest_pid=pid,job=job,
                registration_sha256=hashlib.sha256(registration).hexdigest(),
                scope='supervisor retained pool; child Mach-port IOSurface alias; successful return only')
