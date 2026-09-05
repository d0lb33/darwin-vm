#!/usr/bin/env python3
"""Compare identical, checksummed EL0 ARM loops through QEMU TCG and HVF.

All generated code and logs are isolated under --out; no iOS disk is opened.
The payload uses a real guest MMU and times itself with CNTVCT. Boot setup and
GDB round trips are excluded. These microbenchmarks do not predict boot time.
"""
import argparse
import hashlib
import json
import os
import re
import signal
from pathlib import Path
import socket
import statistics
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/re'))
from smp_trace import Remote


def terminate(signum, frame):
    # Let run()'s finally stop only the QEMU process that this runner owns.
    raise SystemExit(128 + signum)


def assemble(out, source=None, definitions=()):
    obj = out / 'arm_island.o'
    subprocess.run(['clang', '-arch', 'arm64', '-c', *definitions, str(source or Path(__file__).with_name('arm_island.S')), '-o', str(obj)], check=True)
    data = obj.read_bytes()
    pos = 32
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        kind, size = struct.unpack_from('<II', data, pos)
        if kind == 0x19:
            sec = pos + 72
            assert data[sec:sec+16].split(b'\0')[0] == b'__text'
            length, offset = struct.unpack_from('<QI', data, sec+40)
            assert struct.unpack_from('<I', data, sec+60)[0] == 0, 'unexpected relocation'
            return bytearray(data[offset:offset+length])
        pos += size
    raise RuntimeError('no code section')


def run(qemu, payload, case, count, accel, out, *, cpu=None, extra_args=(), extra_env=None, inspect=None):
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
    cmd = [str(qemu), '-M', 'virt,secure=off,virtualization=off', '-cpu', cpu or ('host' if accel == 'hvf' else 'max'),
           '-accel', accel, '-m', '64M', '-L', str(ROOT/'qemu-sptm/pc-bios'),
           '-display', 'none', '-serial', 'none', '-monitor', 'none',
           '-S', '-gdb', f'tcp:127.0.0.1:{port}', '-device',
           f'loader,file={payload},addr=0x40200000,cpu-num=0,force-raw=on']
    cmd.extend(extra_args)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DARWIN_', 'GXFSTAT_'))}
    if extra_env:
        env.update(extra_env)
    remote = None
    with out.with_suffix('.stderr.log').open('w') as log:
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=log)
        try:
            end = time.monotonic()+10
            while time.monotonic() < end:
                if proc.poll() is not None: raise RuntimeError(out.with_suffix('.stderr.log').read_text())
                try: remote = Remote(port); break
                except ConnectionRefusedError: time.sleep(.02)
            if remote is None: raise RuntimeError('GDB startup timed out')
            remote.sock.settimeout(5)
            remote.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            remote.command('?')
            for reg, value in [(0, count), (1, case)]:
                assert remote.command(f'P{reg:x}=' + struct.pack('<Q', value).hex()) == 'OK'
            assert remote.command('Z1,80200100,4') == 'OK'
            remote.send('c')
            try:
                remote.receive()
            except socket.timeout:
                remote.sock.sendall(b'\x03')
                remote.receive()
                xml = ''
                while True:
                    chunk = remote.command(f'qXfer:features:read:system-registers.xml:{len(xml):x},1000')
                    xml += chunk[1:]
                    if chunk[0] == 'l': break
                state = {}
                for name, num in re.findall(r'<reg name="([^"]+)"[^>]*regnum="([^"]+)"', xml):
                    if name in ('ESR_EL1','FAR_EL1','ELR_EL1','TCR_EL1','TTBR0_EL1','SCTLR_EL1'):
                        state[name] = remote.command(f'p{int(num):x}')
                raise RuntimeError('setup timed out: '+str(state))
            assert int.from_bytes(bytes.fromhex(remote.command('p20')), 'little') == 0x80200100
            pstate = int.from_bytes(bytes.fromhex(remote.command('p21')), 'little')
            assert pstate & 15 == 0, f'not EL0: {pstate:x}'
            assert remote.command('z1,80200100,4') == 'OK'
            assert remote.command('Z1,80201000,4') == 'OK'
            t0 = time.monotonic(); remote.send('c'); reply = remote.receive(); elapsed = time.monotonic()-t0
            regs = struct.unpack_from('<33Q', bytes.fromhex(remote.command('g')))
            assert regs[32] == 0x80201000, f'unexpected PC {regs[32]:x} {reply}'
            assert regs[0] == 0 and regs[21] > 0 and regs[22] > 0
            result = {'accel': accel, 'case': case, 'iterations': count, 'seconds': regs[22]/regs[21],
                      'host_seconds': elapsed, 'counter_frequency': regs[21], 'checksum': hex(regs[9]),
                      'pstate': hex(pstate), 'entry_el': regs[23] >> 2, 'command': cmd}
            result['vector_ext_option'] = env.get('QEMU_ARM_TCG_VECTOR_EXT', 'unset')
            if case == 1:
                memory = b''.join(bytes.fromhex(remote.command(f'm{addr:x},400')) for addr in range(0x80220000, 0x80230000, 0x400))
                result['data_sha256'] = hashlib.sha256(memory).hexdigest()
            if inspect is not None:
                inspect(remote, result)
            out.with_suffix('.json').write_text(json.dumps(result, indent=2))
            return result
        finally:
            if remote: remote.sock.close()
            if proc.poll() is None: proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)


def main():
    signal.signal(signal.SIGTERM, terminate)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--qemu', type=Path, default=ROOT/'qemu-sptm/build-fast/qemu-system-aarch64')
    p.add_argument('--repeat', type=int, default=5)
    p.add_argument('--iterations', type=int, default=10000000)
    a = p.parse_args()
    if not 1 <= a.repeat <= 20 or not 1 <= a.iterations <= 100000000: p.error('invalid count')
    a.out.mkdir(exist_ok=False)
    code = assemble(a.out)
    # A 64KiB pointer ring, +17 slots per dependent load, visits every slot.
    for i in range(8192): struct.pack_into('<Q', code, 0x20000+i*8, 0x80220000+((i+17)&8191)*8)
    payload = a.out/'arm_island.bin'; payload.write_bytes(code)
    report = {'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'payload_sha256': hashlib.sha256(code).hexdigest(), 'runs': [], 'host_load': os.getloadavg()}
    labels = ['integer', 'load_store_64K', 'neon', 'pointer_chase_64K']
    for rep in range(a.repeat):
        for case in range(4):
            pair = []
            for accel in (['hvf','tcg'] if rep % 2 == 0 else ['tcg','hvf']):
                result = run(a.qemu.resolve(), payload, case, a.iterations, accel, a.out/f'{rep}_{case}_{accel}')
                report['runs'].append(result); pair.append(result)
                print(f'{rep} {labels[case]} {accel}: {result["seconds"]:.6f}s checksum={result["checksum"]}', flush=True)
                (a.out/'results.json').write_text(json.dumps(report, indent=2))
            assert pair[0]['checksum'] == pair[1]['checksum'], 'arithmetic mismatch'
            assert pair[0].get('data_sha256') == pair[1].get('data_sha256'), 'memory mismatch'
    report['medians'] = {}
    for case, label in enumerate(labels):
        med = {accel: statistics.median(r['seconds'] for r in report['runs'] if r['case'] == case and r['accel'] == accel) for accel in ['hvf','tcg']}
        med['tcg_over_hvf'] = med['tcg']/med['hvf']; report['medians'][label] = med
    (a.out/'results.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report['medians'], indent=2))


if __name__ == '__main__': main()
