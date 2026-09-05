#!/usr/bin/env python3
"""Host-only full-byte-stream bridge check with the actual wrapper and worker."""
import argparse
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import time
from proxy_uart import ProxyUART


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('harness', 'bundle', 'worker', 'library', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(exist_ok=False)
    bridge = ProxyUART(a.worker.resolve(), a.output)
    host, guest = socket.socketpair()
    host.setblocking(False)
    guest.setblocking(False)
    log = (a.output/'harness.stderr').open('wb')
    env = dict(os.environ, DVM_PROXY_FIRST_DELAY_MS='200')
    proc = subprocess.Popen([str(a.harness.resolve()), '--stdio', str(a.bundle.resolve()), str(a.library.resolve())],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, env=env)
    os.set_blocking(proc.stdout.fileno(), False)
    offset, pending, deadline = 0, b'', time.monotonic()+40
    try:
        bridge.line('DVMGPU_READY')
        while proc.poll() is None:
            if time.monotonic()>deadline:
                raise TimeoutError('host-only bridge timeout')
            ready = select.select([proc.stdout, guest], [], [], .001)[0]
            if proc.stdout in ready:
                data = os.read(proc.stdout.fileno(), 65536)
                for start in range(0,len(data),48):
                    piece = data[start:start+48]
                    bridge.line(f'DVMGPU_OUT {offset} {piece.hex()}')
                    offset += len(piece)
            bridge.pump(host)
            if guest in ready:
                pending += guest.recv(65536)
                while b'\n' in pending:
                    line, pending = pending.split(b'\n',1)
                    prefix, off, encoded = line.split()
                    assert prefix==b'DVMGPU_IN'
                    data=bytes.fromhex(encoded.decode())
                    proc.stdin.write(data); proc.stdin.flush()
                    bridge.line(f'DVMGPU_ACK {int(off)+len(data)}')
        assert proc.returncode==0
        bridge.line('DVMGPU_DONE host-test=1')
    finally:
        if proc.poll() is None:
            proc.kill(); proc.wait(timeout=2)
        proc.stdin.close();proc.stdout.close();log.close()
        bridge.close();host.close();guest.close()
    result = next(line.removeprefix('HARNESS_RESULT ') for line in (a.output/'harness.stderr').read_text().splitlines() if line.startswith('HARNESS_RESULT '))
    report = json.loads(result)
    # --stdio runs locally here; do not let its transport flag imply a guest run.
    report['host_only'] = True
    report['test'] = 'host-simulated UART framing'
    assert len(report['runs'])==9 and report['passed']
    (a.output/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':
    main()
