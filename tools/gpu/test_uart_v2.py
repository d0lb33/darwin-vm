#!/usr/bin/env python3
"""Exercise the actual C supervisor and real host Metal worker through socket UART.
Inject corruption + dropped ACKs; delivery must remain byte-exact and once only.
"""
import argparse
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import time
from proxy_uart_v2 import ReliableProxyUART
from verify_roundtrip import verify, require

def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('transport', 'worker', 'harness', 'bundle', 'library', 'cache', 'output'):
        p.add_argument('--' + key, type=Path, required=True)
    p.add_argument('--disconnect', action='store_true')
    a = p.parse_args()
    a.output.mkdir(exist_ok=False)
    bridge = ReliableProxyUART(a.worker, a.output, a.cache, faults=True)
    host, guest = socket.socketpair()
    host.setblocking(False)
    log = (a.output / 'supervisor.stderr').open('wb')
    command = [str(a.transport), '--host-test', str(guest.fileno()), str(a.harness), str(a.bundle), str(a.library)]
    proc = subprocess.Popen(command, pass_fds=(guest.fileno(),), stderr=log, start_new_session=True)
    guest.close()
    deadline = time.monotonic() + 90
    try:
        with (a.output / 'wire.log').open('wb') as wire:
            while proc.poll() is None:
                if time.monotonic() > deadline:
                    raise TimeoutError('host simulated roundtrip deadline')
                bridge.pump(host)
                if select.select([host], [], [], 0.001)[0]:
                    data = host.recv(65536)
                    if not data:
                        break
                    wire.write(data)
                    wire.flush()
                    bridge.feed(data)
                    if a.disconnect and bridge.guest_offset > 300:
                        host.close()
                        break
            proc.wait(timeout=3)
        if a.disconnect:
            require(proc.returncode != 0 and (not bridge.finished), 'proc.returncode!=0 and not bridge.finished')
        else:
            require(proc.returncode == 0 and bridge.finished and (bridge.close_status == 0), 'proc.returncode==0 and bridge.finished and bridge.close_status==0')
            require(b'DVMGPU_READER_STOPPED final_ack=1\n' in (a.output/'wire.log').read_bytes(), 'console reader was not joined before completion')
            require(bridge.duplicates >= 1 and bridge.retries >= 2 and (len(bridge.injected) == 3), 'bridge.duplicates>=1 and bridge.retries>=2 and len(bridge.injected)==3')
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=3)
        host.close()
        log.close()
        bridge.close()
    if a.disconnect:
        result = dict(expected_failure=True, exit_code=proc.returncode, protected_close=False)
        (a.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
        return
    result = verify(a.output)
    result['host_only'] = True
    result['test'] = 'actual C supervisor, real Metal, simulated UART with corruption and ACK loss'
    (a.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(dict(passed=True, operations=len(result['operations']), host_only=True)))
if __name__ == '__main__':
    main()
