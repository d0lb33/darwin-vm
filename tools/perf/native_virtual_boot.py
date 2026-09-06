#!/usr/bin/env python3
"""Bounded real SPTM boot on the experimental virtual-EL2 HVF backend."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
from arm_island_bench import ROOT, Remote, terminate
from native_sptm_tables import read_phys, registers


def main():
    signal.signal(signal.SIGTERM, terminate)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--dtree', type=Path, required=True)
    ap.add_argument('--sptm', type=Path, required=True)
    ap.add_argument('--ledger', type=Path, required=True)
    ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
    ap.add_argument('--seconds', type=int, default=3)
    ap.add_argument('--shadow', action='store_true',
                    help='Enable the experimental persistent shadow context')
    ap.add_argument('--deny-shadow-exec', action='store_true',
                    help='Negative control: remove initial native execute permission')
    ap.add_argument('--check-tables', type=Path,
                    help='Compare stopped guest table pages with a prior capture JSON')
    ap.add_argument('--bootkc', type=Path, help='Adapted kernelcache (default firmware/bootkc)')
    ap.add_argument('--bootargs', help='Guest boot arguments (probe.sh --bootargs)')
    ap.add_argument('--bridge-env', action='append', default=[],
                    help='KEY=VALUE bridge knob passed to QEMU (e.g. QEMU_HVF_VIRTUAL_QUIET=1)')
    ap.add_argument('--memory', action='append', default=[], metavar='PA:SIZE',
                    help='Capture up to 64 KiB of physical guest RAM per range')
    a = ap.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,60}', a.tag) or not 1 <= a.seconds <= 900:
        ap.error('Use a simple tag of at most 60 characters and 1..900 seconds')
    out = Path('/tmp/dvm') / f'{a.tag}.virtual.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    # Private monitor/PID names ensure cleanup cannot target a display guest
    # when probe.sh rejects a colliding public tag or fails before launch.
    run_tag = f'{a.tag}-{secrets.token_hex(8)}'
    pid_file = out.parent / f'{run_tag}.pid'
    with out.open('x') as reservation:
        reservation.write('{}\n')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith('QEMU_HVF_')}
    env.update(QEMU_HVF_VIRTUAL_EL2='1', QEMU_HVF_VIRTUAL_LEDGER=str(a.ledger.resolve()),
               DVM_QEMU=str(a.qemu.resolve()))
    if a.shadow:
        env['QEMU_HVF_VIRTUAL_SHADOW'] = '1'
    for pair in a.bridge_env:
        key, _, value = pair.partition('=')
        env[key] = value
    if a.deny_shadow_exec:
        if not a.shadow:
            ap.error('--deny-shadow-exec requires --shadow')
        env['QEMU_HVF_VIRTUAL_SHADOW_DENY_EXEC'] = '1'
    cmd = [str(ROOT / 'tools/probe.sh'), '--secs', str(a.seconds), '--tag', run_tag,
           '--pid-file', str(pid_file),
           *(['--bootkc', str(a.bootkc.resolve())] if a.bootkc else []),
           *(['--bootargs', a.bootargs] if a.bootargs else []),
           '--dtree', str(a.dtree.resolve()), '--launch-manifest',
           f'/tmp/dvm/{a.tag}.launch.json', '--keep', '--', '-accel',
           'hvf,ipa-bits=40,kernel-irqchip=off', '-cpu', 'host',
           '-sptm', str(a.sptm.resolve()), '-gdb', f'tcp:127.0.0.1:{port}']
    report = {'command': cmd, 'run_tag': run_tag,
              'environment': {k: v for k, v in env.items() if k.startswith('QEMU_HVF_')},
              'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'sptm_sha256': hashlib.sha256(a.sptm.read_bytes()).hexdigest(),
              'ledger_sha256': hashlib.sha256(a.ledger.read_bytes()).hexdigest()}
    try:
        with open(f'/tmp/dvm/{a.tag}.stdout', 'w') as log:
            result = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=a.seconds + 40)
        report['probe_returncode'] = result.returncode
        if result.returncode == 0:
            remote = Remote(port)
            try:
                remote.sock.settimeout(5)
                remote.command('?')
                report['state'] = {k: hex(v) for k, v in registers(
                    remote, ('SPSR_GL', 'ASPSR_GL', 'ELR_GL', 'ESR_GL')).items()}
                if a.memory:
                    assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                    report['memory'] = []
                    for spec in a.memory:
                        pa, size = (int(n, 0) for n in spec.split(':'))
                        assert 0 < size <= 65536 and 0x800000000 <= pa < pa + size <= 0xa00000000
                        data = read_phys(remote, pa, size)
                        report['memory'].append({'pa': hex(pa), 'size': size,
                                                 'data': data.hex(),
                                                 'sha256': hashlib.sha256(data).hexdigest()})
                if a.check_tables:
                    reference = json.loads(a.check_tables.read_text())
                    assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                    checks = []
                    for page in reference['pages']:
                        pa = int(page['pa'], 16)
                        assert pa % 0x4000 == 0 and 0x800000000 <= pa < 0xa00000000
                        digest = hashlib.sha256(read_phys(remote, pa, 0x4000)).hexdigest()
                        checks.append({'pa': page['pa'], 'sha256': digest,
                                       'matches': digest == page['sha256']})
                    report['table_checks'] = checks
                    report['tables_match'] = bool(checks) and all(p['matches'] for p in checks)
                    assert report['tables_match'], 'Table contents differ from the reference'
            finally:
                remote.sock.close()
    finally:
        try:
            if pid_file.exists():
                subprocess.run(['python3', str(ROOT / 'tools/hmp.py'),
                                f'/tmp/dvm/{run_tag}.sock', 'quit'], timeout=10,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finally:
            out.write_text(json.dumps(report, indent=2))
    print(Path(f'/tmp/dvm/{a.tag}.stdout').read_text())
    trace = Path('/tmp/dvm/probe') / f'{run_tag}.stderr.log'
    if trace.exists():
        print('\n'.join(line for line in trace.read_text().splitlines()
                        if 'Virtual ' in line)[-14000:])
    return report.get('probe_returncode', 1)


if __name__ == '__main__':
    raise SystemExit(main())
