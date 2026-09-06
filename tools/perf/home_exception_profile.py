#!/usr/bin/env python3
"""Steady-state exception profile of a restored multi-core guest under TCG.

The gxfstat compatibility counters are compiled out under SMP
(hw/arm/darwin.c disables them), and every home-screen checkpoint is six
cores. This tool instead enables QEMU's CPU_LOG_INT logging at runtime on
an already-restored, paused guest through its HMP monitor, runs bounded
windows (idle, then optional host-side touch activity through QMP), and
counts what the log records per window:

- "Taking exception N [name] ... from ELx" lines: GENTER, SVC, IRQ, FIQ,
  data/instruction aborts and the rest, by name and originating EL;
- "gexit" lines, which pair with GENTER;
- "Exception return" lines (ERET), by level.

Rates are per host second across all vCPUs and per vCPU. GENTER/GEXIT
pairs are the guarded transitions the HVF bridge charges at
docs/re/hvf-fastpath-ceiling.md costs; SVC/IRQ/abort rates are the
uncounted trap classes that note calls out. Register-operation counts are
not observable this way; the migration profile's ratio of 35.8 Apple
register operations per guarded pair is applied as an estimate and labelled
as such in the output.

The tool only issues monitor commands (logfile, log, cont, stop) and QMP
input events; it never writes guest memory or registers, and it leaves the
guest paused at the end. Quit it separately.
"""
import argparse
import collections
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from checkpoint_common import HMP  # noqa: E402

EXC_RE = re.compile(rb'^Taking exception (\d+) \[([^\]]+)\] on CPU (\d+)')
FROM_RE = re.compile(rb'^\.\.\.from EL(\d)')
GEXIT_RE = re.compile(rb'^gexit ')
ERET_RE = re.compile(rb'^Exception return from AArch64 EL(\d) to AArch64 EL(\d)')
REGS_PER_PAIR = 26649997 / 744399   # HVF_MIG_CALLS2, docs/re/hvf-migration-call-profile.md


def count_window(path, start, end):
    counts = collections.Counter()
    per_cpu = collections.Counter()
    pending = None
    with open(path, 'rb') as f:
        f.seek(start)
        remaining = end - start
        while remaining > 0:
            line = f.readline()
            if not line:
                break
            remaining -= len(line)
            m = EXC_RE.match(line)
            if m:
                pending = (m.group(2).decode(), int(m.group(3)))
                per_cpu[int(m.group(3))] += 1
                continue
            m = FROM_RE.match(line)
            if m and pending:
                counts[f'{pending[0]}@EL{m.group(1).decode()}'] += 1
                pending = None
                continue
            if GEXIT_RE.match(line):
                counts['gexit'] += 1
                continue
            m = ERET_RE.match(line)
            if m:
                counts[f'eret EL{m.group(1).decode()}->EL{m.group(2).decode()}'] += 1
    return counts, per_cpu


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--monitor', required=True, help='HMP unix socket of the restored guest')
    ap.add_argument('--qmp', help='QMP unix socket, needed for the activity window')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--seconds', type=int, default=60)
    ap.add_argument('--activity', action='store_true',
                    help='second window with repeated QMP swipes through tools/re/send_touch.py')
    ap.add_argument('--cpus', type=int, default=6)
    a = ap.parse_args()
    a.out.mkdir(exist_ok=False)
    logfile = a.out / 'int.log'
    hmp = HMP(a.monitor, timeout=30)
    status = hmp.command('info status')
    if 'paused' not in status:
        raise SystemExit(f'guest must be paused, got: {status}')
    hmp.command(f'logfile {logfile}')
    hmp.command('log int')
    report = {'monitor': a.monitor, 'seconds': a.seconds, 'cpus': a.cpus,
              'initial_status': status.strip(), 'windows': {}}

    def window(name, activity):
        start = logfile.stat().st_size if logfile.exists() else 0
        t0 = time.monotonic()
        hmp.command('cont')
        deadline = t0 + a.seconds
        swipes = 0
        while time.monotonic() < deadline:
            if activity and a.qmp:
                y = 20000 if swipes % 2 else 12000
                subprocess.run([sys.executable, str(ROOT / 'tools/re/send_touch.py'),
                                '--from', '16000', str(y), '--to', '16000', str(32000 - y),
                                '--duration', '0.4', a.qmp],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                swipes += 1
                time.sleep(1.0)
            else:
                time.sleep(min(0.5, deadline - time.monotonic()))
        hmp.command('stop')
        elapsed = time.monotonic() - t0
        end = logfile.stat().st_size
        counts, per_cpu = count_window(logfile, start, end)
        genter = sum(v for k, v in counts.items() if k.startswith('genter@'))
        pairs = min(genter, counts.get('gexit', 0))
        total = sum(v for k, v in counts.items() if '@' in k)
        rows = {k: {'count': v, 'per_second': v / elapsed,
                    'per_second_per_cpu': v / elapsed / a.cpus}
                for k, v in sorted(counts.items(), key=lambda kv: -kv[1])}
        est_regs = pairs * REGS_PER_PAIR / elapsed
        report['windows'][name] = {
            'elapsed': elapsed, 'log_bytes': end - start, 'swipes': swipes,
            'exceptions_total': total, 'exceptions_per_second': total / elapsed,
            'genter': genter, 'gexit': counts.get('gexit', 0),
            'pairs_per_second': pairs / elapsed,
            'pairs_per_second_per_cpu': pairs / elapsed / a.cpus,
            'estimated_apple_regops_per_second': est_regs,
            'estimated_apple_regops_per_second_per_cpu': est_regs / a.cpus,
            'per_cpu_exceptions': dict(sorted(per_cpu.items())),
            'by_kind': rows,
        }
        (a.out / 'results.json').write_text(json.dumps(report, indent=2))
        print(json.dumps({k: v for k, v in report['windows'][name].items()
                          if k != 'by_kind'}, indent=2))
        for k, v in list(rows.items())[:12]:
            print(f"  {k:28s} {v['count']:9d}  {v['per_second']:10.1f}/s  {v['per_second_per_cpu']:9.1f}/s/cpu")

    window('idle', False)
    if a.activity:
        window('activity', True)
    hmp.command('log none')
    report['final_status'] = hmp.command('info status').strip()
    (a.out / 'results.json').write_text(json.dumps(report, indent=2))
    print('guest left paused; results in', a.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
