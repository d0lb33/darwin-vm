"""Small metadata and independent assembled-host-output evidence for blur runs."""
import hashlib,json,struct,re,zlib
from pathlib import Path
SIZES=((64,64),(256,256),(512,512),(1184,2560))
REQUEST=0x31514c42
REPLY=0x31504c42

def request(raw):
    if len(raw)<192:raise ValueError('blur frame short')
    magic,version,seq,n,w,h,size,pipeline,source,tmp,out=struct.unpack_from('<IIQ4I4Q',raw)
    if magic!=REQUEST or version!=1 or not seq or n!=len(raw) or not w or not h or w%32 or h%32 or w>256 or h>256 or size!=(w+32)*(h+32)*8 or n!=192+size:raise ValueError('blur input frame')
    return dict(op='blurSubmit',seq=seq,w=w,h=h,pipeline=pipeline,input=source,tmp=tmp,output=out,state=raw[64:192].hex(),input_sha=hashlib.sha256(raw[192:]).hexdigest())

def reply(raw):
    if len(raw)<64:raise ValueError('blur reply short')
    magic,version,seq,n,status,gpu,out,w,h=struct.unpack_from('<IIQIIQQII',raw)
    if magic!=REPLY or version!=1 or not seq or status!=4 or n!=len(raw) or not out or not w or not h or w%32 or h%32 or w>256 or h>256 or n!=64+w*h*8 or any(raw[48:64]):raise ValueError('blur output frame')
    return dict(seq=seq,ok=True,status=4,dispatches=2,gpu_ns=gpu,output=out,w=w,h=h,output_sha=hashlib.sha256(raw[64:]).hexdigest())

class BlurEvidence:
    def __init__(self,out):self.out=Path(out);self.size=0;self.frame=0;self.tile=0;self.frames=[];self.capture=set();self.pixels=None
    def add(self,req,rep,raw,output):
        if self.size>=len(SIZES):raise ValueError('excess blur submissions')
        w,h=SIZES[self.size];tiles=[(x,y,min(256,w-x),min(256,h-y)) for y in range(0,h,256) for x in range(0,w,256)]
        x,y,tw,th=tiles[self.tile]
        if (req['w'],req['h'],rep['w'],rep['h'])!=(tw,th,tw,th) or req['output']!=rep['output']:raise ValueError('blur tile shape or output identity')
        if self.pixels is None:self.pixels=bytearray(w*h*8)
        for row in range(th):self.pixels[((y+row)*w+x)*8:((y+row)*w+x+tw)*8]=output[64+row*tw*8:64+(row+1)*tw*8]
        if (tw,th) not in self.capture:
            self.capture.add((tw,th));(self.out/f'blur-request-{tw}x{th}.bin').write_bytes(raw);(self.out/f'blur-reply-{tw}x{th}.bin').write_bytes(output)
        self.tile+=1
        if self.tile==len(tiles):
            r=dict(width=w,height=h,run=self.frame,sha=hashlib.sha256(self.pixels).hexdigest(),last_seq=req['seq'],tiles=len(tiles))
            self.frames.append(r)
            with (self.out/'blur-host-frames.jsonl').open('a') as f:f.write(json.dumps(r)+'\n')
            self.tile=0;self.frame+=1
            if self.frame==17:self.frame=0;self.size+=1;self.pixels=None

def verify(out,events,records):
    out=Path(out);lines=[e['line'] for e in events]
    frames=[dict(re.findall(r'(\w+)=([^ ]+)',line)) for line in lines if line.startswith('GPU_LOAD_BLUR_FRAME ')]
    host=[json.loads(x) for x in (out/'blur-host-frames.jsonl').read_text().splitlines()]
    if len(frames)!=68 or len(host)!=68:raise ValueError('missing blur frames')
    for a,b in zip(frames,host):
        if any(int(a[k])!=b[k] for k in ('width','height','run')) or a['sha']!=b['sha']:raise ValueError('guest pixels differ from assembled host output')
    submits=[r for r in records if r['op']=='blurSubmit']
    if len(submits)!=952 or any(not r['reply'].get('ok') for r in records):raise ValueError('blur submission failure/count')
    if records[-1]['op']!='stats' or records[-1]['reply']['live']['objects'] or records[-1]['reply']['live']['resourceBytes'] or records[-1]['reply']['submissions']!=952 or records[-1]['reply'].get('creations')!=17:raise ValueError('blur retirement')
    if (out/'driver-worker.log').read_text().count('dispatches=2 status=4')!=952:raise ValueError('blur GPU completion count')
    raw=(out/'shared-ram.bin').read_bytes();head,=struct.unpack_from('<Q',raw,0x180)
    if not 0<head<=120:raise ValueError('blur audit count')
    audit=[]
    for i in range(head):
        seq,n,c=struct.unpack_from('<QII',raw,0x1000+i*512);data=raw[0x1010+i*512:0x1010+i*512+n]
        if seq!=i+1 or not 0<n<480 or zlib.crc32(data)!=c:raise ValueError('blur audit CRC')
        audit.append(data.decode().strip())
    if audit!=[e['line'] for e in events if e.get('source')=='shared-ram-audit'] or audit[-1]!='GPU_LOAD_COMPLETE result=pass scope=metal-driver-blur submissions=952 resources=0':raise ValueError('blur guest completion audit')
    readiness=json.loads((out/'driver-readiness.json').read_text());status=json.loads((out/'input-status.json').read_text())
    if not readiness.get('fresh_ack') or readiness.get('stable_seconds')!=10 or status.get('guest_state')!='R' or status.get('presents',0)<readiness['presents'] or status.get('acked',0)<=readiness['input_status']['acked']:raise ValueError('blur display/input readiness')
    return dict(scope='process-local-metal-driver-blur',submissions=952,dispatches=1904,frames=68,live_resources=0,readiness=readiness)
