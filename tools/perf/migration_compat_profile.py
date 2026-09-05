#!/usr/bin/env python3
"""Boot a fresh TCG disk child and count 60 seconds of early Data metadata work.

One vCPU because gxfstat is single-writer scaffolding. QMP pauses bracket
read-only host LLDB counter snapshots. No guest debugger or counter resets.
The loop stops at the deadline, panic or exit. Early boot complete precedes
the measured metadata work on this image and is not a migration-done marker.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
from arm_island_bench import ROOT, terminate


class QMP:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect(str(path))
        self.file = self.sock.makefile('rb')
        assert 'QMP' in json.loads(self.file.readline())
        self.command('qmp_capabilities')

    def command(self, name):
        self.sock.sendall(json.dumps({'execute': name}).encode() + b'\n')
        while True:
            row = json.loads(self.file.readline())
            if 'error' in row:
                raise RuntimeError(row['error'])
            if 'return' in row:
                return row['return']

    def close(self):
        self.file.close()
        self.sock.close()


def main():
    signal.signal(signal.SIGTERM, terminate)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--seconds', type=int, default=60)
    ap.add_argument('--parent', type=Path, default=Path('/tmp/dvm/data-seed/rebuild/marker.qcow2'))
    ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
    a = ap.parse_args()
    if not 1 <= a.seconds <= 60:
        ap.error('sample must be 1..60 seconds')
    a.out.mkdir(exist_ok=False)
    fw = ROOT / 'firmware'
    dt = Path('/tmp/dvm/data-seed/dt_nvme_welcome.bin')
    tc = Path.home() / 'dvm-artifacts/tc/merged_sysvol_cryptex_tc.bin'
    for p in (a.parent, a.qemu, dt, tc):
        assert p.is_file(), p
    chain = json.loads(subprocess.check_output(['qemu-img', 'info', '--backing-chain',
                                               '--output=json', str(a.parent)]))
    disk = a.out / 'data-child.qcow2'
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2',
                    '-b', str(a.parent.resolve()), str(disk)], check=True)
    serial = a.out / 'serial.log'
    monitor = a.out / 'qmp.sock'
    cmd = [str(a.qemu.resolve()), '-M', 'darwin', '-cpu', 'max', '-smp', '1',
           '-accel', 'tcg,thread=multi', '-m', '12G', '-display', 'none',
           '-serial', f'file:{serial}', '-monitor', 'none',
           '-qmp', f'unix:{monitor},server=on,wait=off',
           '-dtree', str(dt), '-tc', str(tc), '-fb', '1179x2556', '-fbmode', 'graphics',
           '-drive', f'if=none,id=ans,file={disk},format=qcow2',
           '-args', 'rootdev=disk1s1 ignition_level=1 launchd_unsecure_cache=1 serial=3 -v wdt=-1 wlan-olyhal-abort']
    for option, name in (('-bootkc', 'bootkc'), ('-sptm', 'sptm'),
                         ('-txm', 'txm'), ('-ramdisk', 'ramdisk.dmg')):
        cmd += [option, str(fw / name)]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('DARWIN_', 'GXFSTAT_', 'QEMU_HVF_'))}
    # Same existing early-migration launch settings as smp_boot_bench.py.
    env.update(DARWIN_DCP_EPIC='all', DARWIN_DCP_REPLY='1', DARWIN_DCP_IOMFB='4',
        DARWIN_DCP_IOMFB_OUT='A401=01,A000=01,A454=01000000,A033=4152474200000000000000000000000000000000000000000000000000000000000000000000000001000000,A453=9b040000fc090000,A412=01000000',
        DARWIN_DCP_IOMFB_CB='D120::4,D586:9b040000fc090000:4')
    report = dict(command=cmd, env={k: v for k, v in env.items() if k.startswith('DARWIN_')},
                  qemu_sha256=hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
                  parent_chain=chain, requested_seconds=a.seconds, cpus=1, passed=False)
    progress = re.compile(rb'set_dir_stats:\d+: (disk1s[25]) setting dir-stats for ino (\d+) ')
    def save():
        (a.out / 'results.json').write_text(json.dumps(report, indent=2))
    def snapshot(label):
        output = a.out / f'{label}.json'
        commands = a.out / f'{label}.lldb'
        commands.write_text('command script import ' + str(Path(__file__).with_name('host_gxf_snapshot.py')) + '\n' +
                            f'script host_gxf_snapshot.capture(lldb.debugger, {str(output)!r}, {child.pid})\nprocess detach\nquit\n')
        with (a.out / f'{label}.lldb.log').open('w') as log:
            subprocess.run(['lldb', '--batch', '-p', str(child.pid), '-s', str(commands)],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)
        return json.loads(output.read_text())
    qmp = None
    with (a.out / 'stderr.log').open('w') as log:
        child = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=log)
        report['pid'] = child.pid
        save()
        launch = time.monotonic()
        try:
            while time.monotonic() - launch < 180:
                if child.poll() is not None:
                    raise RuntimeError('QEMU exited before migration')
                text = serial.read_bytes() if serial.exists() else b''
                if b'panic(cpu' in text:
                    raise RuntimeError('Guest panic before migration')
                if progress.search(text):
                    report['migration_marker_seconds'] = time.monotonic() - launch
                    break
                time.sleep(.05)
            else:
                raise RuntimeError('No migration marker within 180 seconds')
            qmp = QMP(monitor)
            assert len(qmp.command('query-cpus-fast')) == 1
            qmp.command('stop')
            assert qmp.command('query-status')['status'] == 'paused'
            initial = snapshot('start')
            report['initial_serial_bytes'] = serial.stat().st_size
            report['initial_events'] = len(set(progress.findall(serial.read_bytes())))
            qmp.command('cont')
            start = time.monotonic()
            next_progress = start + 10
            print(f'Migration sample started after {report["migration_marker_seconds"]:.3f}s boot; owned PID {child.pid}', flush=True)
            save()
            while time.monotonic() - start < a.seconds:
                if child.poll() is not None:
                    raise RuntimeError('QEMU exited during sample')
                text = serial.read_bytes()
                if b'panic(cpu' in text:
                    report['stop_reason'] = 'guest panic'
                    break
                if time.monotonic() >= next_progress:
                    print(f'Sampling {time.monotonic()-start:.1f}s: {len(set(progress.findall(text)))} distinct metadata events', flush=True)
                    next_progress += 10
                time.sleep(.02)
            qmp.command('stop')
            report['active_seconds'] = time.monotonic() - start
            assert qmp.command('query-status')['status'] == 'paused'
            final = snapshot('end')
            report.setdefault('stop_reason', '60-second migration window' if a.seconds == 60 else 'bounded migration window')
            report['delta'] = {k: final['counters'][k] - initial['counters'][k] for k in final['counters']}
            report['registers'] = []
            for name, values in final['registers'].items():
                old = initial['registers'].get(name, dict(reads=0, writes=0))
                row = {k: values[k] - old[k] for k in ('reads', 'writes')}
                assert min(row.values()) >= 0
                if sum(row.values()):
                    report['registers'].append(dict(name=name, **row, total=sum(row.values())))
            report['registers'].sort(key=lambda r: r['total'], reverse=True)
            for name in ('exc', 'genter_el', 'gexit_el', 'sysreg_el'):
                report[name] = [b - a for a, b in zip(initial[name], final[name])]
            assert sum(r['reads'] for r in report['registers']) == report['delta']['sysreg_rd']
            assert sum(r['writes'] for r in report['registers']) == report['delta']['sysreg_wr']
            report['final_events'] = len(set(progress.findall(serial.read_bytes())))
            report['passed'] = report['stop_reason'].endswith('migration window')
            print(json.dumps({k: report[k] for k in ('active_seconds', 'stop_reason', 'delta', 'initial_events', 'final_events')}, indent=2), flush=True)
        except Exception as exc:
            report['error'] = repr(exc)
            print(report['error'], flush=True)
        finally:
            if qmp:
                qmp.close()
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            report['process_returncode'] = child.returncode
            save()
    return int(not report['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
