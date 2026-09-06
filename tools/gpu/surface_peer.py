"""Owned NS6 mailbox peer; delegates all successful replies to real host Metal.

One outstanding submission. Output is published before its CRC-protected reply.
The input remains guest-owned until that reply. No filesystem on NS6 is mounted.
"""
import hashlib
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import time
import zlib

AIR_SHA = '8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364'
BYTES = 12288
PAGE = 4096


def checked_packet(packet, identity):
    if len(packet) != PAGE or packet[:64] != identity:
        return None
    if zlib.crc32(packet[:-4]) != struct.unpack_from('<I', packet, PAGE-4)[0]:
        return None  # A publication racing the reader is retried, never executed.
    seq, nonce, length, crc = struct.unpack_from('<IIII', packet, 64)
    if seq not in (1, 2, 3) or length != BYTES or any(packet[80:-4]):
        raise ValueError('invalid surface request contract')
    return seq, nonce, crc


class SurfacePeer:
    def __init__(self, out, worker, library):
        self.out = Path(out)
        self.started = time.monotonic()
        self.seen = set()
        self.records = []
        self.header = (b'DVM-SURFACE-DEMO-v1\0'+os.urandom(32)).ljust(64, b'\0')
        self.fd = -1
        self.proc = None
        self.log = None
        self.buffer = b''
        self.ready = False
        self.worker = Path(worker).resolve()
        self.library = Path(library).resolve()
        data = self.library.read_bytes()
        if len(data) != 2705796 or hashlib.sha256(data).hexdigest() != AIR_SHA:
            raise ValueError('exact guest AIR cache mismatch')
        self.log = (self.out/'metal-worker.log').open('xb')
        self.fd = os.open(self.out/'aux.raw', os.O_CREAT|os.O_EXCL|os.O_RDWR, 0o600)
        os.ftruncate(self.fd, 64*1024*1024)
        os.pwrite(self.fd, self.header+bytes.fromhex(AIR_SHA), 0)
        (self.out/'surface-host-inputs.json').write_text(json.dumps(dict(
            worker=str(self.worker),worker_sha256=hashlib.sha256(self.worker.read_bytes()).hexdigest(),
            library=str(self.library),library_sha256=AIR_SHA),indent=2)+'\n')

    def read(self, count=None):
        deadline = time.monotonic()+15
        while True:
            end = self.buffer.find(b'\n')+1 if count is None else count
            if end and len(self.buffer) >= end:
                result, self.buffer = self.buffer[:end], self.buffer[end:]
                return result
            remaining = deadline-time.monotonic()
            if remaining <= 0 or not select.select([self.proc.stdout], [], [], remaining)[0]:
                raise TimeoutError('host Metal worker reply exceeded 15 seconds')
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError('host Metal worker closed: '+str(self.proc.poll()))
            self.buffer += chunk

    def command(self, request, expected):
        self.proc.stdin.write(request)
        self.proc.stdin.flush()
        got = self.read()
        if got != expected:
            raise RuntimeError(f'Metal protocol expected {expected!r}, received {got!r}')

    def start_worker(self):
        env = os.environ.copy()
        env['DVM_PROXY_LIBRARY_CACHE'] = str(self.library)
        self.proc = subprocess.Popen([str(self.worker)],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=self.log,env=env)
        self.command(f'LIBREF 1 2705796 {AIR_SHA}\n'.encode(), b'OK 1\n')
        self.command(b'PIPE 2 read_write_surf_compute\n', b'OK 2\n')
        self.ready = True

    def pump(self):
        packet = os.pread(self.fd, PAGE, 0x10000)
        fields = checked_packet(packet, self.header)
        if fields is None:
            return
        seq, nonce, crc = fields
        if seq in self.seen:
            if packet != self.last_packet:
                raise ValueError('published request mutated before next sequence')
            return
        if seq != len(self.seen)+1:
            raise ValueError('out-of-order surface request')
        began = time.monotonic_ns()
        data = os.pread(self.fd, BYTES, 0x100000)
        expected = bytes((i*17+(i>>8)*31+seq*43+(nonce>>((i%4)*8)))&255 for i in range(BYTES))
        if zlib.crc32(data) != crc or data != expected:
            raise ValueError('guest input CRC or nonce-dependent pixel oracle failed')
        if not self.ready:
            self.start_worker()
        ident = seq+2
        self.command(f'RUN {ident} {seq} 64 48 0 12288\n'.encode()+data,
            f'DATA {ident} 12288\n'.encode())
        output = self.read(BYTES)
        if output != data:
            raise ValueError('real Metal output differs from guest input')
        # Data goes first; guest validates CRC and bytes after seeing this completion.
        if os.pwrite(self.fd, output, 0x200000) != BYTES:
            raise OSError('short output publication')
        response = bytearray(packet)
        struct.pack_into('<I', response, 80, zlib.crc32(output))
        struct.pack_into('<I', response, PAGE-4, zlib.crc32(response[:-4]))
        if os.pwrite(self.fd, response, 0x20000) != PAGE:
            raise OSError('short completion publication')
        self.seen.add(seq)
        self.last_packet = packet
        record = dict(sequence=seq,nonce=nonce,bytes=BYTES,
            input_sha256=hashlib.sha256(data).hexdigest(),output_sha256=hashlib.sha256(output).hexdigest(),
            crc32=f'{zlib.crc32(output):08x}',host_service_ms=(time.monotonic_ns()-began)/1e6)
        self.records.append(record)
        (self.out/f'input-{seq}.bgra').write_bytes(data)
        (self.out/f'output-{seq}.bgra').write_bytes(output)
        with (self.out/'surface-host.jsonl').open('a') as f:
            f.write(json.dumps(record)+'\n')

    def verify(self):
        if self.seen != {1,2,3}:
            raise ValueError('missing three real Metal submissions')
        if os.pread(self.fd,96,0) != self.header+bytes.fromhex(AIR_SHA):
            raise ValueError('host-owned session header changed')
        log = (self.out/'metal-worker.log').read_text()
        for seq in (1,2,3):
            lines = [s for s in log.splitlines() if f'event=run id={seq+2} generation={seq} ' in s]
            if len(lines)!=1 or 'status=4 ' not in lines[0] or 'pre_dispatch_differs=1' not in lines[0]:
                raise ValueError('missing real Metal completion/negative-control evidence')
        return dict(scope='guest-surface-host-metal',submissions=3,bytes_per_surface=BYTES,
            air_sha256=AIR_SHA,records=self.records)

    def finish(self):
        if not self.proc:
            raise RuntimeError("Metal worker never started")
        self.proc.stdin.close()
        if self.proc.wait(timeout=3) != 0:
            raise RuntimeError("Metal worker exited unsuccessfully")

    def close(self):
        if self.proc:
            if not self.proc.stdin.closed:
                try:
                    self.proc.stdin.close()
                except (BrokenPipeError,OSError):
                    pass  # The workload verdict already records protocol failure.
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill(); self.proc.wait(timeout=3)
            self.proc.stdout.close()
        if self.log:
            self.log.close()
        if self.fd >= 0:
            os.close(self.fd)
