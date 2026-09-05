"""Bounded, diagnostic UART bridge; one outstanding host-to-guest frame.

The worker sees the actual guest byte stream. This module never substitutes a
local shader or fabricates GPU results. UART is deliberately not a performance
transport; missing/duplicate offsets abort instead of replaying GPU commands.
"""
import json
import os
from pathlib import Path
import re
import select
import subprocess
import time


class ProxyUART:
    def __init__(self, executable, output, library_cache=None):
        self.output = Path(output)
        self.errors = (self.output/'host-worker.stderr').open('wb')
        env=os.environ.copy()
        if library_cache is not None:
            env['DVM_PROXY_LIBRARY_CACHE']=str(library_cache)
        self.proc = subprocess.Popen([str(executable)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.errors,env=env)
        os.set_blocking(self.proc.stdin.fileno(), False)
        os.set_blocking(self.proc.stdout.fileno(), False)
        self.requests = (self.output/'guest-requests.bin').open('wb')
        self.responses = (self.output/'host-responses.bin').open('wb')
        self.trace = (self.output/'bridge.jsonl').open('w')
        self.to_worker = bytearray()
        self.to_guest = bytearray()
        self.guest_offset = self.response_offset = 0
        self.pending_ack = None
        self.ready = False
        self.finished = False

    def event(self, **fields):
        self.trace.write(json.dumps(dict(monotonic=time.monotonic(), **fields))+'\n')
        self.trace.flush()

    def line(self, line):
        if 'DVMGPU_READY' in line:
            self.ready = True
            self.event(event='ready')
        if 'DVMGPU_OUT ' in line:
            frame = line[line.index('DVMGPU_OUT '):]
            match = re.fullmatch(r'DVMGPU_OUT ([0-9]+) ([0-9a-f]{2,96})', frame)
            if not match or len(match[2]) % 2:
                raise RuntimeError(f'malformed request frame: {frame!r}')
            offset, data = int(match[1]), bytes.fromhex(match[2])
            if offset != self.guest_offset:
                raise RuntimeError(f'guest byte offset {offset}, expected {self.guest_offset}')
            self.guest_offset += len(data)
            self.requests.write(data)
            self.to_worker += data
        if 'DVMGPU_ACK ' in line:
            frame = line[line.index('DVMGPU_ACK '):]
            match = re.fullmatch(r'DVMGPU_ACK ([0-9]+)', frame)
            if not match or self.pending_ack is None or int(match[1]) != self.pending_ack[0]:
                raise RuntimeError(f'unexpected ACK: {frame!r}')
            self.response_offset = int(match[1])
            self.pending_ack = None
        if 'DVMGPU_DONE' in line:
            self.finished = True
            self.event(event='done', line=line, request_bytes=self.guest_offset,
                response_bytes=self.response_offset)

    def pump(self, uart):
        if self.finished:
            return
        if self.proc.poll() is not None:
            raise RuntimeError(f'host worker exited {self.proc.returncode}')
        if len(self.to_worker) > 16*1024*1024 or len(self.to_guest) > 1024*1024:
            raise RuntimeError('bridge buffer limit exceeded')
        readable, writable, _ = select.select([self.proc.stdout],
            [self.proc.stdin] if self.to_worker else [], [], 0)
        if writable:
            n = os.write(self.proc.stdin.fileno(), self.to_worker[:65536])
            del self.to_worker[:n]
        if readable:
            data = os.read(self.proc.stdout.fileno(), 65536)
            if not data:
                raise RuntimeError('host response pipe closed')
            self.responses.write(data)
            self.responses.flush()
            self.to_guest += data
        if self.pending_ack and time.monotonic()-self.pending_ack[1] > 60:
            raise RuntimeError(f'guest response ACK timeout at {self.pending_ack[0]}')
        if self.ready and self.to_guest and self.pending_ack is None:
            data = bytes(self.to_guest[:48])
            frame = f'DVMGPU_IN {self.response_offset} {data.hex()}\n'.encode()
            # AF_UNIX frames are tiny; never silently retry a partial send.
            n = uart.send(frame)
            if n != len(frame):
                raise RuntimeError(f'partial UART send {n}/{len(frame)}')
            del self.to_guest[:len(data)]
            self.pending_ack = (self.response_offset+len(data), time.monotonic())

    def close(self):
        self.event(event='close', request_bytes=self.guest_offset,
            acknowledged_response_bytes=self.response_offset, finished=self.finished)
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)
        self.proc.stdout.close()
        for stream in (self.requests, self.responses, self.errors, self.trace):
            stream.close()
