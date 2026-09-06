#!/usr/bin/env python3
"""Enumerate the privileged operations unmodified SPTM performs under TCG.

The GDB single-step tracer (native_sptm_trace.py) is bounded to 4096 steps.
This tool instead runs a fresh TCG child with QEMU's ``-d in_asm,int`` log,
which records every translation block in first-execution order together with
every exception the CPU takes. It then extracts the instructions the virtual
EL2 bridge has to arbitrate: system-register accesses, GENTER/GEXIT, ERET,
SMC/HVC, TLB/cache maintenance, PSTATE immediates, and exception entries.

The result is the ordered list of bridge gates between a start point and the
first instruction executed outside the SPTM image, or the end of the run.
This describes firmware's requested operations, not hardware semantics. It
issues no debugger writes and controls only its own child process.
"""
import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

from arm_island_bench import ROOT, terminate

# Runtime relocation of the SPTM image: original 0xfffffff027... -> 0xfffffff007...
SPTM_ORIGINAL_BASE = 0xfffffff027000000
SPTM_RUNTIME_BASE = 0xfffffff007000000
SPTM_RUNTIME_END = 0xfffffff007200000

ASM_RE = re.compile(r'^0x([0-9a-f]+):\s+([0-9a-f]{8})\s+(.*)$')
EXC_RE = re.compile(r'^Taking exception (\d+) \[([^\]]+)\]')
EXC_ELR_RE = re.compile(r'^\.\.\. from EL(\d) PC 0x([0-9a-f]+)')
EXC_TO_RE = re.compile(r'^\.\.\.\s+to EL(\d) PC 0x([0-9a-f]+)')


def classify(word, text):
    """Return a gate category for an instruction, or None for ordinary code."""
    if word in (0x00201400,) or (word & 0xfffffff0) == 0x00201420:
        return 'genter' if word != 0x00201400 else 'gexit'
    if word == 0xd69f03e0:
        return 'eret'
    if (word & 0xffe0001f) == 0xd4000003:
        return 'smc'
    if (word & 0xffe0001f) == 0xd4000002:
        return 'hvc'
    if (word & 0xffe0001f) == 0xd4000001:
        return 'svc'
    if (word & 0xfff80000) == 0xd5080000 or (word & 0xfff80000) == 0xd5000000:
        # SYS / SYSL (op0=1): TLBI, IC, DC, AT
        op1 = (word >> 16) & 7
        if (word & 0xfff00000) in (0xd5080000, 0xd5000000) and ((word >> 19) & 3) == 1:
            return 'sys'
    if (word & 0xffd00000) == 0xd5100000 or (word & 0xffd00000) == 0xd5300000:
        return 'msr' if not (word & (1 << 21)) else 'mrs'
    if (word & 0xfff8f01f) == 0xd500401f:
        return 'pstate'
    if word == 0xd50340df or (word & 0xfffff0ff) == 0xd50340df or (word & 0xfffff0ff) == 0xd50340ff:
        return 'pstate'
    return None


def encoding(word):
    return [(word >> 19) & 3, (word >> 16) & 7, (word >> 12) & 15,
            (word >> 8) & 15, (word >> 5) & 7]


def main():
    signal.signal(signal.SIGTERM, terminate)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--dtree', type=Path, required=True)
    ap.add_argument('--start-pc', type=lambda x: int(x, 0), default=None,
                    help='Only report gates at or after the first block containing this PC')
    ap.add_argument('--seconds', type=int, default=120)
    ap.add_argument('--qemu', type=Path,
                    default=ROOT / 'qemu-sptm/build/qemu-system-aarch64')
    ap.add_argument('--stop-on-serial', action='store_true',
                    help='Stop as soon as the guest writes to the serial console')
    a = ap.parse_args()
    a.out.mkdir(exist_ok=False)
    log = a.out / 'qemu.log'
    serial = a.out / 'serial.log'
    cmd = [str(a.qemu.resolve()), '-M', 'darwin', '-cpu', 'max',
           '-accel', 'tcg', '-smp', '1', '-m', '8G', '-display', 'none',
           '-monitor', 'none', '-serial', f'file:{serial.resolve()}',
           '-d', 'in_asm,int', '-D', str(log.resolve()),
           '-dtree', str(a.dtree.resolve())]
    for option, name in (('-bootkc', 'bootkc'), ('-sptm', 'sptm'), ('-txm', 'txm'),
                         ('-tc', 'ramdisk.tc'), ('-ramdisk', 'ramdisk.dmg')):
        cmd += [option, str(ROOT / 'firmware' / name)]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('QEMU_HVF_', 'DARWIN_', 'GXFSTAT_'))}
    report = {'command': cmd, 'passed': False,
              'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'sptm_sha256': hashlib.sha256((ROOT / 'firmware/sptm').read_bytes()).hexdigest(),
              'dtree_sha256': hashlib.sha256(a.dtree.read_bytes()).hexdigest()}
    with (a.out / 'stderr.log').open('w') as err:
        child = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=err)
        started = time.monotonic()
        try:
            while time.monotonic() - started < a.seconds:
                if child.poll() is not None:
                    break
                if a.stop_on_serial and serial.exists() and serial.stat().st_size > 0:
                    report['stop_reason'] = 'serial output'
                    break
                time.sleep(0.25)
            else:
                report['stop_reason'] = 'deadline'
        finally:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            report['process_returncode'] = child.returncode
            report['elapsed'] = time.monotonic() - started

    gates = []
    exceptions = []
    started_reporting = a.start_pc is None
    first_outside = None
    order = 0
    with log.open('r', errors='replace') as f:
        for line in f:
            line = line.rstrip('\n')
            m = ASM_RE.match(line)
            if m:
                pc = int(m.group(1), 16)
                word = int(m.group(2), 16)
                text = m.group(3).strip()
                order += 1
                if not started_reporting:
                    if pc == a.start_pc:
                        started_reporting = True
                    else:
                        continue
                if first_outside is None and not (SPTM_RUNTIME_BASE <= pc < SPTM_RUNTIME_END):
                    first_outside = {'order': order, 'pc': hex(pc), 'word': hex(word), 'text': text}
                kind = classify(word, text)
                if kind:
                    item = {'order': order, 'pc': hex(pc), 'word': hex(word),
                            'kind': kind, 'text': text,
                            'in_sptm': SPTM_RUNTIME_BASE <= pc < SPTM_RUNTIME_END}
                    if kind in ('msr', 'mrs', 'sys'):
                        item['encoding'] = encoding(word)
                    gates.append(item)
                continue
            m = EXC_RE.match(line)
            if m and started_reporting:
                exceptions.append({'order': order, 'index': int(m.group(1)),
                                   'name': m.group(2)})
                continue
            m = EXC_ELR_RE.match(line)
            if m and exceptions and started_reporting:
                exceptions[-1].update(from_el=int(m.group(1)),
                                      from_pc=hex(int(m.group(2), 16)))
                continue
            m = EXC_TO_RE.match(line)
            if m and exceptions and started_reporting:
                exceptions[-1].update(to_el=int(m.group(1)),
                                      to_pc=hex(int(m.group(2), 16)))
    report.update(blocks=order, gates=gates, exceptions=exceptions,
                  first_outside_sptm=first_outside,
                  serial_bytes=serial.stat().st_size if serial.exists() else 0)
    # Distinct gate summary in first-occurrence order.
    seen = {}
    for g in gates:
        key = (g['kind'], g['text'].split(None, 1)[0] if g['kind'] == 'sys' else
               (g['text'] if g['kind'] in ('msr', 'mrs') else g['kind']))
        if key not in seen:
            seen[key] = {'kind': g['kind'], 'text': g['text'], 'first_order': g['order'],
                         'first_pc': g['pc'], 'count': 0}
        seen[key]['count'] += 1
    report['distinct'] = list(seen.values())
    report['passed'] = child.returncode in (0, -15, None) and order > 0
    (a.out / 'results.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report.get(k) for k in
                      ('passed', 'stop_reason', 'elapsed', 'blocks', 'serial_bytes',
                       'first_outside_sptm')}))
    print(f'{len(gates)} gate instructions, {len(report["distinct"])} distinct, '
          f'{len(exceptions)} exceptions in {a.out}')
    return int(not report['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
