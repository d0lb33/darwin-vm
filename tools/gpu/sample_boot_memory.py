#!/usr/bin/env python3
"""Sample RSS of one owned GPU run's QEMU and worker, without guest RPCs.

RSS is process residency, not a live Metal allocation count. Samples begin at
invocation, may miss brief peaks, and do not establish sustained memory stability.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('run', type=Path)
    p.add_argument('--seconds', type=int, default=180)
    a = p.parse_args()
    if not 1 <= a.seconds <= 600:
        p.error('bounded sampling deadline must be 1..600 seconds')
    qemu = int((a.run / 'qemu.pid').read_text())
    expected = json.loads((a.run / 'launch.json').read_text())['argv'][0]
    worker = json.loads((a.run / 'driver-inputs.json').read_text())['worker']
    started = time.monotonic()
    with (a.run / 'process-memory.jsonl').open('x') as output:
        while time.monotonic() - started < a.seconds:
            rows = {}
            raw = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,rss=,comm='], text=True)
            for line in raw.splitlines():
                fields = line.strip().split(None, 3)
                if len(fields) == 4:
                    rows[int(fields[0])] = dict(parent=int(fields[1]), rss_kib=int(fields[2]), command=fields[3])
            if qemu not in rows:
                break
            if Path(rows[qemu]['command']).resolve() != Path(expected).resolve():
                raise ValueError('owned QEMU PID identity changed')
            parent = rows[qemu]['parent']
            workers = [(pid, row) for pid, row in rows.items() if row['parent'] == parent and Path(row['command']).resolve() == Path(worker).resolve()]
            if len(workers) != 1:
                raise ValueError('expected exactly one owned sibling worker')
            pid, row = workers[0]
            output.write(json.dumps(dict(monotonic_ns=time.monotonic_ns(), sample_elapsed_s=time.monotonic()-started,
                                         qemu_pid=qemu, qemu_rss_kib=rows[qemu]['rss_kib'], worker_pid=pid, worker_rss_kib=row['rss_kib']))+'\n')
            output.flush()
            time.sleep(1)


if __name__ == '__main__':
    main()
