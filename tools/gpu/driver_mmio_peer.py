"""One-session MMIO notification peer. Payloads live in owned shared RAM.

Socket readiness joins the runner's select set: no mailbox polling interval.
The inherited verifier checks actual Metal completions and a nonce CPU oracle.
"""
import hashlib
import base64
import json
import mmap
import os
from pathlib import Path
import socket
import struct
import subprocess
import time
import zlib
from driver_peer import DriverPeer, MAX
from surface_peer import AIR_SHA
from driver_binary import REQUEST, REPLY, decode_request, decode_reply
import blur_peer
import present_peer

RAM_SIZE=0x1000000
MAGIC=0x44564d31
# Mirror of driver_capabilities.h (test_driver_mmio_peer checks the values).
# Staged bulk transfers: the guest copies raw bytes to STAGING_OFFSET and the
# framed JSON request carries {offset,length,crc}; the peer verifies the CRC
# and hands the worker the base64 field it always accepted. Published only in
# the present/managed (mode 3) layout, whose framed requests stay under 64 KiB.
STAGING_OFFSET=0x20000
STAGING_BYTES=0x1E0000
STAGING_DESCRIPTOR=0x340
STAGING_MAGIC=int.from_bytes(b'DVMSTAG1','little')
STAGING_FLAG_ENABLED=1
STAGING_FLAG_RAM_POLL=2
WORKER_FRAME=4*1024*1024

class MMIOPeer(DriverPeer):
    def __init__(self,out,worker,library,boot=False,library_cache=None):
        self.out=Path(out);self.started=time.monotonic();self.records=[];self.seen=set()
        self.ready_since=None;self.ready_identity=None;self.ready_acks=0
        self.released=False;self.released_at=None;self.buffer=b'';self.rx=b'';self.sock=None
        self.library=Path(library);self.worker=Path(worker);self.boot=boot;self.audit_seen=0
        mode=self.worker.parent/"transport-mode.txt"
        self.managed=mode.exists() and mode.read_text().strip()=="--mmio-present-pool"
        self.present=self.managed or (mode.exists() and mode.read_text().strip()=="--mmio-present")
        self.audit_limit=120 if self.present or (mode.exists() and mode.read_text().strip()=="--mmio-blur") else 64
        self.reply_offset=0x200000 if self.present else 0x800000
        # DVM_TRANSPORT_FLAGS: bit 0 staging, bit 1 guest RAM polling (default both).
        self.staging_flags=int(os.environ.get('DVM_TRANSPORT_FLAGS','3'),0) if self.present else 0
        self.staged_requests=0;self.staged_bytes=0
        self.max_bytes=0x10000 if self.present else MAX
        raw=self.library.read_bytes()
        if len(raw)!=2705796 or hashlib.sha256(raw).hexdigest()!=AIR_SHA:raise ValueError('exact AIR cache mismatch')
        self.fd=os.open(self.out/'shared-ram.bin',os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600)
        os.ftruncate(self.fd,RAM_SIZE);self.ram=mmap.mmap(self.fd,RAM_SIZE)
        self.log=(self.out/'driver-worker.log').open('xb')
        env={k:v for k,v in os.environ.items() if k!='DVM_DRIVER_BOOTSTRAP'}
        if self.present:env['DVM_DRIVER_PRESENT_RAM']=str(self.out/'shared-ram.bin')
        else:env.pop('DVM_DRIVER_PRESENT_RAM',None)
        for key in ('DVM_DRIVER_MANAGED_RAM','DVM_DRIVER_MANAGED_PAGES','DVM_DRIVER_MANAGED_DELAY_US'):env.pop(key,None)
        if self.managed:
            fd=os.open(self.out/'managed-ram.bin',os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600)
            os.ftruncate(fd,0x300000000);os.close(fd)
            env['DVM_DRIVER_MANAGED_RAM']=str(self.out/'managed-ram.bin')
            env['DVM_DRIVER_MANAGED_PAGES']=str(self.out/'managed-pages.bin')
        env.update(DVM_DRIVER_LIBRARY=str(self.library),DVM_DRIVER_BOOTSTRAP='1')
        env.pop('DVM_DRIVER_LIBRARY_CACHE',None)
        if library_cache is not None:
            cache=Path(library_cache).resolve()
            manifest=(cache/'manifest.json').read_bytes()
            for item in json.loads(manifest)['libraries']:
                digest=item['sha256']
                if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('library cache digest')
                data=(cache/(digest+'.metallib')).read_bytes()
                if len(data)!=item['bytes'] or hashlib.sha256(data).hexdigest()!=digest:raise ValueError('library cache input changed')
            (self.out/'library-cache.json').write_bytes(manifest)
            env['DVM_DRIVER_LIBRARY_CACHE']=str(cache)
        self.worker_env=env.copy()
        self.proc=subprocess.Popen([str(self.worker)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,env=env)
        (self.out/'driver-inputs.json').write_text(json.dumps(dict(worker=str(self.worker),worker_sha256=hashlib.sha256(self.worker.read_bytes()).hexdigest(),library=str(self.library),air_sha256=AIR_SHA,transport='shared-ram-mmio'),indent=2)+'\n')
        try:
            length,=struct.unpack('<I',self.read(4))
            if not 0<length<=4096:raise ValueError('bootstrap length')
            self.bootstrap=json.loads(self.read(length))
            if self.bootstrap.get('bootstrap')!=1 or self.bootstrap.get('protocol')!='DVM-METAL-DRIVER-v1' or self.bootstrap.get('queue') is not True or not self.bootstrap.get('device'):
                raise ValueError('Metal bootstrap contract')
        except BaseException:
            self.close();raise
    def release(self,evidence):
        self.ram[0x100:0x120]=bytes.fromhex(AIR_SHA)
        if self.staging_flags:
            struct.pack_into('<4Q',self.ram,STAGING_DESCRIPTOR,STAGING_MAGIC,STAGING_OFFSET,STAGING_BYTES,self.staging_flags)
        if hasattr(self,'present_config'):
            frames,hz=self.present_config
            struct.pack_into('<4I',self.ram,0x200,1,frames,hz,0)
        self.sock.sendall(struct.pack('<QII',0,0,0))
        self.released=True;self.released_at=time.monotonic()
        evidence.update(elapsed=self.released_at-self.started,bootstrap=self.bootstrap)
        (self.out/'driver-readiness.json').write_text(json.dumps(evidence,indent=2)+'\n')
    def pump(self):
        if self.sock is None:
            path=self.out/'gpu-notify.sock'
            if not path.exists() or struct.unpack_from('<II',self.ram,0)!=(MAGIC,1):return
            self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            self.sock.settimeout(1);self.sock.connect(str(path));self.sock.setblocking(False)
            self.header=self.ram[16:32]
            if not any(self.header):raise ValueError('missing QEMU session')
        if not self.released:
            if self.boot:self.release(dict(scope='host-metal-device-and-queue-before-guest-workload'))
            else:self.gate()
        try:data=self.sock.recv(16-len(self.rx))
        except BlockingIOError:return
        if not data:raise RuntimeError('MMIO notification disconnected')
        self.rx+=data
        if len(self.rx)<16:return
        seq,n,checksum=struct.unpack('<QII',self.rx);self.rx=b''
        if not self.released or seq!=len(self.seen)+1 or not 0<n<=self.max_bytes:raise ValueError('MMIO notification contract')
        expected=self.header+struct.pack('<QII',seq,n,checksum)
        if self.ram[0x40:0x60]!=expected or self.ram[16:32]!=self.header:raise ValueError('MMIO request header/session')
        raw=self.ram[0x10000:0x10000+n]
        if zlib.crc32(raw)!=checksum:raise ValueError('MMIO request CRC')
        blur=struct.unpack_from("<I",raw)[0]==blur_peer.REQUEST
        binary=struct.unpack_from("<I",raw)[0]==REQUEST
        request=dict(seq=struct.unpack_from("<Q",raw,8)[0],op="blurSubmit" if blur else "submit") if binary or blur else json.loads(raw)
        if not isinstance(request,dict) or request.get('seq')!=seq:raise ValueError('MMIO request inner sequence')
        control_bytes=n;staged_bytes=0
        if not (binary or blur) and 'staged' in request:
            raw,request,staged_bytes=self.unstage(request)
            n=len(raw)
            if n>WORKER_FRAME:raise ValueError('staged worker frame extent')
        started=time.monotonic_ns()
        control=self.control_reply(request) if hasattr(self,'control_reply') else None
        if control is None:
            self.proc.stdin.write(struct.pack('<I',n)+raw);self.proc.stdin.flush()
            length,=struct.unpack('<I',self.read(4))
            if not 0<length<=self.max_bytes:raise ValueError('host reply length')
            output=self.read(length);reply=blur_peer.reply(output) if struct.unpack_from("<I",output)[0]==blur_peer.REPLY else decode_reply(output) if struct.unpack_from("<I",output)[0]==REPLY else json.loads(output)
        else:
            reply=dict(control,seq=seq,ok=True);output=json.dumps(reply).encode();length=len(output)
            if length>self.max_bytes:raise ValueError('runner reply extent')
        if not isinstance(reply,dict) or reply.get('seq')!=seq:raise ValueError('host reply sequence')
        self.ram[self.reply_offset:self.reply_offset+length]=output
        self.ram[0x80:0xa0]=self.header+struct.pack('<QII',seq,length,zlib.crc32(output))
        self.sock.sendall(struct.pack('<QII',seq,0,0))
        service_us=(time.monotonic_ns()-started)/1000
        self.seen.add(seq)
        if blur:
            request=blur_peer.request(raw)
            if not hasattr(self,"blur_evidence"):self.blur_evidence=blur_peer.BlurEvidence(self.out)
            if reply.get("ok"):self.blur_evidence.add(request,reply,raw,output)
        if binary:request=decode_request(raw)  # Evidence conversion follows completion publication.
        record=dict(wire_encoding="blur-v1" if blur else "binary-v1" if binary else "json",seq=seq,op=request.get('op'),request_bytes=control_bytes,reply_bytes=length,
            host_service_us=service_us,host_received_ns=started,host_completed_ns=time.monotonic_ns(),
            request={k:v for k,v in request.items() if k!='data'},reply=reply)
        if staged_bytes:record['staged_bytes']=staged_bytes
        if request.get('op') in ('writeRenderBuffer','upload','writeTextureChunk') and 'data' in request:
            # Generated resource contents are replayable; Apple libraries stay
            # referenced by their verified hash and separate local AIR cache.
            name=f'render-upload-{seq:06d}.json'
            (self.out/name).write_text(json.dumps(request)+'\n')
            record['upload_file']=name
            record['upload_sha256']=hashlib.sha256((self.out/name).read_bytes()).hexdigest()
        if binary:
            (self.out/f"binary-request-{seq:04d}.bin").write_bytes(raw)
            (self.out/f"binary-reply-{seq:04d}.bin").write_bytes(output)
        self.records.append(record)
        with (self.out/'driver-host.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
    def unstage(self,request):
        """Replace a staged descriptor with the base64 field the worker expects.

        Returns (worker request bytes, rebuilt request, payload length). Any
        contract violation is a protocol failure of this session, like a bad
        framed CRC: raise, and the worker never sees the request."""
        if not self.staging_flags&STAGING_FLAG_ENABLED:raise ValueError('staged request without staging')
        d=request['staged']
        if not isinstance(d,dict):raise ValueError('staged descriptor')
        try:offset,length,crc=int(d['offset']),int(d['length']),int(d['crc'])
        except (KeyError,TypeError,ValueError):raise ValueError('staged descriptor')
        if offset<0 or length<=0 or offset+length>STAGING_BYTES or not 0<=crc<2**32:raise ValueError('staged extent')
        payload=bytes(self.ram[STAGING_OFFSET+offset:STAGING_OFFSET+offset+length])
        if zlib.crc32(payload)!=crc:raise ValueError('staged payload CRC')
        rebuilt={k:v for k,v in request.items() if k!='staged'}
        rebuilt['data']=base64.b64encode(payload).decode()
        self.staged_requests+=1;self.staged_bytes+=length
        return json.dumps(rebuilt).encode(),rebuilt,length
    def audit(self):
        if self.sock is None:return []
        if self.ram[16:32]!=self.header:raise ValueError('audit session changed')
        head,=struct.unpack_from('<Q',self.ram,0x180)
        limit=getattr(self,'audit_limit',64);ring=getattr(self,'runner',False)
        if head<self.audit_seen or (head-self.audit_seen if ring else head)>limit:raise ValueError('audit head bounds')
        lines=[]
        while self.audit_seen<head:
            expected=self.audit_seen+1;offset=0x1000+(self.audit_seen%limit if ring else self.audit_seen)*512
            seq,n,c=struct.unpack_from('<QII',self.ram,offset)
            if seq!=expected or not 0<n<480:raise ValueError('audit slot framing')
            raw=self.ram[offset+16:offset+16+n]
            if zlib.crc32(raw)!=c:raise ValueError('audit CRC')
            line=raw.decode('utf-8').strip()
            if not line.startswith('GPU_LOAD_') or '\n' in line:raise ValueError('audit record format')
            self.audit_seen=seq;lines.append(line)
            # Preserve the exact consumed header/payload BEFORE acknowledging
            # the slot. The producer may then reuse it; final RAM alone cannot
            # verify a job spanning more than one ring revolution.
            slot=struct.pack('<QII',seq,n,c)+raw
            capture=dict(head=head,session=self.header.hex(),bytes=base64.b64encode(slot).decode())
            with (self.out/'driver-audit.jsonl').open('a') as f:f.write(json.dumps(dict(seq=seq,line=line,slot_v1=capture))+'\n')
            if ring:struct.pack_into('<Q',self.ram,0x188,self.audit_seen)
        return lines
    def verify(self,events):
        if getattr(self,'consumer',False):
            from consumer_verify import verify
            return verify(self.out,events,self.records)
        if self.present:return present_peer.verify(self.out,events,self.records)
        if hasattr(self,"blur_evidence"):return blur_peer.verify(self.out,events,self.records)
        return super().verify(events)
    def close(self):
        if self.sock:self.sock.close()
        self.ram.close()
        super().close()
