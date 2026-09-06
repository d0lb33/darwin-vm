"""Acceptance ledger: guest completion, identified normal scanouts, final pixels."""
import hashlib,json,re,struct,time,zlib
from pathlib import Path
WIDTH,HEIGHT,ROW=1179,2556,4864
BYTES=ROW*HEIGHT

def fields(line):return dict(re.findall(r'(\w+)=([^ ]+)',line))
def observe_native_recovery(out,offset,started):
    with (Path(out)/'stderr.log').open('rb') as f:
        f.seek(offset);data=f.read()
    first=data.find(b'iomfb: presented ')
    if b'iomfb: gpu-present frame=' in data:raise ValueError('GPU frame appeared after completed batch')
    if first<0 or b'D594 nested completed, status 0x0' not in data[first:]:return None
    return dict(verified=True,after_batch=True,log_offset=offset,host_started_ns=started,
        host_observed_ns=time.monotonic_ns(),scope='new normal scanout and native completion appended after the batch recovery gate',evidence=data[first:].decode(errors='replace'))
def verify_recovery(out):
    out=Path(out)
    before=json.loads((out/'present-recovery-baseline.json').read_text())
    after=json.loads((out/'driver-display.json').read_text())
    status=after['input_status']
    if not after.get('fresh_ack') or after.get('stable_seconds')!=10 or status.get('guest_state')!='R' or after.get('presents',0)<=before.get('presents',0) or status.get('acked',0)<=before.get('acked',0):
        raise ValueError('no fresh native presentation/input recovery after batch')
    witness=json.loads((out/'post-batch-native.json').read_text())
    if not witness.get('verified') or not witness.get('after_batch') or not observe_native_recovery(out,witness['log_offset'],witness['host_started_ns']):
        raise ValueError('post-batch native scanout/completion witness missing')
    return after
def verify(out,events,records):
    out=Path(out);lines=[e['line'] for e in events if e.get('source')=='shared-ram-audit']
    raw=(out/'shared-ram.bin').read_bytes();head,=struct.unpack_from('<Q',raw,0x180)
    if not 0<head<=120:raise ValueError('present audit extent')
    audit=[]
    for i in range(head):
        seq,n,c=struct.unpack_from('<QII',raw,0x1000+i*512);data=raw[0x1010+i*512:0x1010+i*512+n]
        if seq!=i+1 or not 0<n<480 or zlib.crc32(data)!=c:raise ValueError('present audit CRC/order')
        audit.append(data.decode().strip())
    if audit!=lines or lines[-1]!='GPU_LOAD_COMPLETE result=pass scope=metal-driver-present submissions=33 resources=0':raise ValueError('present guest completion')
    frames=[fields(x) for x in lines if x.startswith('GPU_LOAD_PRESENT_FRAME ')]
    draws=[r for r in records if r['op']=='residentDraw']
    if [int(x['frame']) for x in frames]!=list(range(1,34)) or [r['request']['frame'] for r in draws]!=list(range(1,34)):raise ValueError('present frame sequence')
    if any(not r['reply'].get('ok') for r in records) or any(r['reply'].get('status')!=4 or r['reply'].get('dispatches')!=3 for r in draws):raise ValueError('present GPU completion')
    ops=[r['op'] for r in records]
    if ops.count('residentCreate')!=1 or ops.count('residentVerify')!=1 or ops.index('residentVerify')<max(i for i,v in enumerate(ops) if v=='residentDraw'):raise ValueError('present setup/final-only verification')
    stats=records[-1]['reply']
    if records[-1]['op']!='stats' or stats['live']['objects'] or stats['live']['resourceBytes'] or stats['submissions']!=33 or stats['creations']!=2:raise ValueError('present retirement')
    log=(out/'stderr.log').read_text(errors='replace');witnesses=re.findall(r'iomfb: gpu-present frame=(\d+) swap=(\d+) dva=(0x[0-9a-f]+) monotonic_ns=(\d+)',log)
    if [int(x[0]) for x in witnesses]!=list(range(1,34)):raise ValueError('missing/duplicate normal GPU scanout frames')
    if any(int(w[1])!=int(f['swap']) for w,f in zip(witnesses,frames)):raise ValueError('display swap identity')
    last=log.rfind('iomfb: gpu-present frame=33 ')
    if 'D594 nested completed, status 0x0' not in log[last:] or 'iomfb: presented ' not in log[last:]:raise ValueError('missing native completion or presentation recovery')
    ready=json.loads((out/'driver-readiness.json').read_text());status=json.loads((out/'input-status.json').read_text())
    if not ready.get('fresh_ack') or ready.get('stable_seconds')!=10 or status.get('guest_state')!='R' or status.get('acked',0)<=ready['input_status']['acked']:raise ValueError('native readiness/recovery')
    if 'GPU_LOAD_PRESENT_POWER_RESET request=0 rc=0x0' not in lines:raise ValueError('display power reset request failed')
    verify_recovery(out)
    return dict(scope='resident-blur-GPU-conversion-normal-DCP-presentation',frames=33,dispatches=99,verification_reads_in_batch=0,frames_metadata=frames,scanouts=[dict(frame=int(a),swap=int(b),dva=c,host_ns=int(d)) for a,b,c,d in witnesses])

def verify_final(out,events):
    out=Path(out);final=[fields(e['line']) for e in events if e['line'].startswith('GPU_LOAD_PRESENT_FINAL ')]
    if len(final)!=1 or final[0].get('frame')!='33' or final[0].get('verified')!='1' or final[0].get('bad_pixels')!='0':raise ValueError('guest final pixel oracle')
    presented=(out/'last-presented.bgra').read_bytes();ram=(out/'shared-ram.bin').read_bytes()[0x300000:0x300000+BYTES]
    if len(presented)!=BYTES or presented!=ram or hashlib.sha256(presented).hexdigest()!=final[0]['sha']:raise ValueError('display pixels differ from verified guest surface / GPU mapping')
    if struct.unpack_from('<4I',presented)!=(0xff44564d,0xff505253,0xff424c52,0xff000021):raise ValueError('final scanout frame marker')
    log=(out/'stderr.log').read_text(errors='replace')
    if f'iomfb: gpu-present-export frame=33 count=33 bytes={BYTES} ok=1' not in log:raise ValueError('post-batch normal scanout export')
    return dict(verified=True,frame=33,bytes=BYTES,sha256=final[0]['sha'],scope='last actual scanout equals GPU shared output and CPU-verified guest IOSurface')
