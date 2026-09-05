#!/usr/bin/env python3
"""Bounded read-only QMP running/debug-stop and new-presentation sample."""
import argparse
import collections
import json
from pathlib import Path
import socket
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qmp', required=True)
    p.add_argument('--log', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--seconds', type=float, default=20)
    a = p.parse_args()
    if not 1 <= a.seconds <= 60:
        p.error('seconds must be 1..60')
    statuses = collections.Counter()
    with open(a.log,'rb') as log, socket.socket(socket.AF_UNIX) as sock:
        log.seek(0,2)
        start_offset = log.tell()
        sock.settimeout(5)
        sock.connect(a.qmp)
        wire = sock.makefile('rwb',buffering=0)
        json.loads(wire.readline())
        def call(command):
            wire.write(json.dumps({'execute':command,'id':'perf'}).encode()+b'\n')
            while True:
                reply = json.loads(wire.readline())
                if reply.get('id') == 'perf':
                    if 'error' in reply:
                        raise RuntimeError(reply['error'])
                    return reply['return']
        call('qmp_capabilities')
        start = time.monotonic()
        while time.monotonic()-start < a.seconds:
            statuses[call('query-status')['status']] += 1
            time.sleep(.1)
        elapsed = time.monotonic()-start
        data = log.read()
    result = {'elapsed':elapsed,'statuses':dict(statuses),
              'running_fraction':statuses['running']/sum(statuses.values()),
              'presentations':data.count(b'iomfb: presented '),
              'new_log_bytes':len(data),'log_start_offset':start_offset}
    result['presentations_per_second'] = result['presentations']/elapsed
    Path(a.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
