"""Owned NS6 command transport for the process-local Metal driver.

Versioned session, monotonic sequence, CRC, bounded framing, one outstanding
RPC; pixels/resources remain private to this helper's host worker process.
"""
import hashlib
import base64
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import time
import zlib
from surface_peer import AIR_SHA, PAGE

MAX = 2*1024*1024

def packet_fields(packet, identity):
    if len(packet)!=PAGE or packet[:64]!=identity or zlib.crc32(packet[:-4])!=struct.unpack_from('<I',packet,PAGE-4)[0]:
        return None
    seq,length,crc=struct.unpack_from('<QII',packet,64)
    if not seq or not 0<length<=MAX or any(packet[80:-4]):
        raise ValueError('invalid driver packet')
    return seq,length,crc

class DriverPeer:
    def __init__(self,out,worker,library):
        self.out=Path(out);self.started=time.monotonic();self.records=[];self.seen=set()
        self.header=(b'DVM-METAL-DRIVER-v1\0'+os.urandom(32)).ljust(64,b'\0')
        self.last_packet=None;self.ready_since=None;self.ready_identity=None;self.ready_acks=0;self.released=False;self.buffer=b''
        self.library=Path(library);self.worker=Path(worker)
        raw=self.library.read_bytes()
        if len(raw)!=2705796 or hashlib.sha256(raw).hexdigest()!=AIR_SHA:raise ValueError('exact AIR cache mismatch')
        self.fd=os.open(self.out/'aux.raw',os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600)
        os.ftruncate(self.fd,64*1024*1024);os.pwrite(self.fd,self.header+bytes.fromhex(AIR_SHA),0)
        self.log=(self.out/'driver-worker.log').open('xb')
        self.proc=subprocess.Popen([str(self.worker)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,
            env={**os.environ,'DVM_DRIVER_LIBRARY':str(self.library)})
        (self.out/'driver-inputs.json').write_text(json.dumps(dict(worker=str(self.worker),worker_sha256=hashlib.sha256(self.worker.read_bytes()).hexdigest(),library=str(self.library),air_sha256=AIR_SHA),indent=2)+'\n')
    def read(self,n):
        until=time.monotonic()+15
        while len(self.buffer)<n:
            left=until-time.monotonic()
            if left<=0 or not select.select([self.proc.stdout],[],[],left)[0]:raise TimeoutError('driver host worker deadline')
            data=os.read(self.proc.stdout.fileno(),65536)
            if not data:raise RuntimeError('driver host worker EOF')
            self.buffer+=data
        out,self.buffer=self.buffer[:n],self.buffer[n:];return out
    def gate(self):
        if self.released:return
        try:status=json.loads((self.out/'input-status.json').read_text())
        except (OSError,ValueError):status={}
        presents=(self.out/'stderr.log').read_text(errors='replace').count('iomfb: presented ')
        if presents and status.get('guest_state')=='R':
            identity=(status.get('guest_pid'),status.get('guest_epoch'))
            if self.ready_since is None or identity!=self.ready_identity:
                self.ready_since=time.monotonic();self.ready_identity=identity;self.ready_acks=status.get('acked',0)
            if time.monotonic()-self.ready_since>=10 and status.get('acked',0)>self.ready_acks:
                os.pwrite(self.fd,struct.pack('<I',1),96);self.released=True
                (self.out/'driver-readiness.json').write_text(json.dumps(dict(elapsed=time.monotonic()-self.started,presents=presents,input_status=status,stable_seconds=10,fresh_ack=True,scope='native-presentation-and-helper-ready-not-home-or-gesture'),indent=2)+'\n')
        else:self.ready_since=None
    def pump(self):
        self.gate()
        packet=os.pread(self.fd,PAGE,0x10000);fields=packet_fields(packet,self.header)
        if fields is None:return
        seq,n,crc=fields
        if seq in self.seen:
            if packet!=self.last_packet:raise ValueError('request mutated after execution')
            return
        if not self.released or seq!=len(self.seen)+1:raise ValueError('request before readiness or out of order')
        raw=os.pread(self.fd,n,0x100000)
        if zlib.crc32(raw)!=crc:raise ValueError('request payload CRC')
        request=json.loads(raw)
        if not isinstance(request,dict) or request.get('seq')!=seq:raise ValueError('request inner sequence')
        started=time.monotonic_ns();self.proc.stdin.write(struct.pack('<I',n)+raw);self.proc.stdin.flush()
        length,=struct.unpack('<I',self.read(4))
        if not 0<length<=MAX:raise ValueError('host reply length')
        output=self.read(length);reply=json.loads(output)
        if not isinstance(reply,dict) or reply.get('seq')!=seq:raise ValueError('host reply sequence')
        if os.pwrite(self.fd,output,0x400000)!=length:raise OSError('short driver payload publication')
        result=bytearray(PAGE);result[:64]=self.header;struct.pack_into('<QII',result,64,seq,length,zlib.crc32(output));struct.pack_into('<I',result,PAGE-4,zlib.crc32(result[:-4]))
        if os.pwrite(self.fd,result,0x20000)!=PAGE:raise OSError('short driver completion publication')
        self.last_packet=packet;self.seen.add(seq)
        record=dict(seq=seq,op=request.get('op'),request_bytes=n,reply_bytes=length,host_service_us=(time.monotonic_ns()-started)//1000,request={k:v for k,v in request.items() if k!='data'},reply=reply)
        if 'data' in request:
            record['upload_sha256']=hashlib.sha256(raw).hexdigest()
            decoded=base64.b64decode(request['data'],validate=True)
            # Preserve generated workload data, never an Apple shader library.
            if request.get('op')=='upload':
                name=f'driver-upload-{seq:04d}.json'
                (self.out/name).write_text(json.dumps(dict(data=request['data'],encoding='base64',
                    resource='texture' if 'texture' in request else 'buffer'))+'\n')
                record['upload_file']=name
                record['upload_bytes_sha256']=hashlib.sha256(decoded).hexdigest()
                record['upload_encoding']='raw resource bytes; texture is RGBA16Float, buffers are bytes'
        self.records.append(record)
        with (self.out/'driver-host.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
    def verify(self,events):
        import base64,re
        runs=[e['line'] for e in events if 'GPU_LOAD_DRIVER_RUN ' in e['line']]
        submits=[r for r in self.records if r['op']=='submit']
        reads=[r for r in self.records if r['op']=='read' and 'buffer' in r['request'] and len(base64.b64decode(r['reply'].get('data','')))==16]
        if len(runs)!=8 or len(submits)!=8 or len(reads)!=8:raise ValueError('missing eight two-pass guest results')
        for i,(row,submit,read) in enumerate(zip(runs,submits,reads),1):
            expected=','.join(f'{n:08x}' for n in struct.unpack('<4I',base64.b64decode(read['reply']['data'])))
            if not re.search(rf'run={i} nonce=\d+ verified=1 dispatches=2 .*result={expected}$',row) or submit['reply'].get('status')!=4 or submit['reply'].get('dispatches')!=2:
                raise ValueError('guest result differs from actual host output')
            nonce=int(re.search(r'nonce=(\d+)',row)[1])
            oracle=struct.pack('<4f',*[1440+3072*((nonce>>(c*4))&7) for c in range(4)])
            if base64.b64decode(read['reply']['data'])!=oracle:raise ValueError('independent nonce oracle mismatch')
        final=self.records[-1]
        if final['op']!='stats' or final['reply']['live']['objects']!=0 or final['reply']['submissions']!=8 or final['reply']['creations']!=6:raise ValueError('resource lifetime or reuse mismatch')
        if any(not r['reply'].get('ok') for r in self.records):raise ValueError('host reported an error')
        log=(self.out/'driver-worker.log').read_text()
        if log.count('dispatches=2 status=4')!=8:raise ValueError('missing real GPU command completions')
        return dict(scope='process-local-metal-driver-luma',submissions=8,dispatches=16,live_resources=0,air_sha256=AIR_SHA,readiness=json.loads((self.out/'driver-readiness.json').read_text()))
    def finish(self):
        self.proc.stdin.close()
        if self.proc.wait(timeout=5)!=0:raise RuntimeError('host worker exit')
    def close(self):
        if not self.proc.stdin.closed:self.proc.stdin.close()
        try:self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait()
        self.proc.stdout.close();self.log.close();os.close(self.fd)
