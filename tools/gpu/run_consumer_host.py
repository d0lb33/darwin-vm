#!/usr/bin/env python3
"""Bounded host QuartzCore rehearsal with replayable JSON requests/replies."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import time


def read(stream, n, deadline):
    data = bytearray()
    while len(data) < n:
        left = deadline-time.monotonic()
        if left <= 0 or not select.select([stream], [], [], left)[0]:
            raise TimeoutError('host rehearsal deadline')
        chunk = os.read(stream.fileno(), n-len(data))
        if not chunk:
            if not data:
                return None
            raise ValueError('short frame')
        data += chunk
    return bytes(data)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('client', 'worker', 'library', 'out'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args(); a.out.mkdir(exist_ok=False)
    env = {k:v for k,v in os.environ.items() if k not in ('DVM_DRIVER_BOOTSTRAP', 'DVM_CLIENT_AUDIT')}
    env.update(DVM_DRIVER_LIBRARY=str(a.library.resolve()), DVM_REHEARSAL_AIR=str(a.library.resolve()))
    processes = []; start = time.monotonic(); error = None
    try:
        with (a.out/'client.log').open('xb') as cl, (a.out/'worker.log').open('xb') as wl, (a.out/'records.jsonl').open('x') as records:
            worker = subprocess.Popen([str(a.worker)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=wl, env=env); processes.append(worker)
            client = subprocess.Popen([str(a.client), 'consumer', str(a.library), '1'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=cl, env=env); processes.append(client)
            for _ in range(10000):
                head = read(client.stdout, 4, start+60)
                if head is None: break
                n, = struct.unpack('<I', head)
                if not 0 < n <= 2*1024*1024: raise ValueError('request extent')
                request = read(client.stdout, n, start+60)
                worker.stdin.write(head+request); worker.stdin.flush()
                head = read(worker.stdout, 4, start+60)
                n, = struct.unpack('<I', head)
                if not 0 < n <= 2*1024*1024: raise ValueError('reply extent')
                reply = read(worker.stdout, n, start+60)
                records.write(json.dumps(dict(request=json.loads(request), reply=json.loads(reply)))+'\n'); records.flush()
                client.stdin.write(head+reply); client.stdin.flush()
            else: raise ValueError('record budget')
            client.wait(timeout=5); worker.stdin.close(); worker.wait(timeout=5)
            if client.returncode or worker.returncode: raise RuntimeError('host rehearsal process failed')
    except Exception as e:
        error = str(e)
    finally:
        for proc in processes:
            if proc.poll() is None: proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait()
        result = dict(scope='host QuartzCore with explicit guest-AIR substitution; not guest evidence',
                      seconds=time.monotonic()-start, passed=error is None, error=error,
                      exits=[proc.returncode for proc in processes],
                      inputs={str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in (a.client,a.worker,a.library)})
        (a.out/'result.json').write_text(json.dumps(result,indent=2)+'\n'); print(json.dumps(result))
    if error: raise SystemExit(1)


if __name__ == '__main__': main()
