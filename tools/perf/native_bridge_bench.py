#!/usr/bin/env python3
"""Benchmark synthetic loops through the current integrated HVF bridge / TCG.

Boot real SPTM to the existing pre-SPRR fixture boundary, install a disposable
payload, and perform SPRR/GXF setup through guest instructions. Only the loop
is timed, using host monotonic time around one GDB continue/stop. A zero-count
control measures that measurement floor. Native diagnostics remain enabled.
This is not an iOS boot benchmark or a hardware-semantics conformance test.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import statistics
import struct
import subprocess
import time
from arm_island_bench import ROOT, Remote, assemble, terminate
from native_sptm_tables import read_phys, registers

PC, PA = 0xfffffff0070a37dc, 0x8070a37dc
STOP_PC, STOP_PA = 0xfffffff0070a4000, 0x8070a4000
TABLES = (0x807024000, 0x807028000, 0x80702c000,
          0x807110000, 0x807114000)


def run(a, accel, case, count, name, payload, ledger):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    cmd = [str(a.qemu.resolve()), '-M', 'darwin', '-cpu',
           'host' if accel == 'hvf' else 'max', '-accel',
           'hvf,ipa-bits=40,kernel-irqchip=off' if accel == 'hvf' else 'tcg',
           '-smp', '1', '-m', '8G', '-display', 'none', '-serial', 'none',
           '-monitor', 'none', '-dtree', str(a.dtree.resolve()), '-S',
           '-gdb', f'tcp:127.0.0.1:{port}', '-sptm',
           str(a.sptm.resolve() if accel == 'hvf' else ROOT / 'firmware/sptm')]
    for option, file in (('-bootkc', 'bootkc'), ('-txm', 'txm'),
                         ('-tc', 'ramdisk.tc'), ('-ramdisk', 'ramdisk.dmg')):
        cmd += [option, str(ROOT / 'firmware' / file)]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('QEMU_HVF_', 'DARWIN_', 'GXFSTAT_'))}
    if accel == 'hvf':
        env.update(QEMU_HVF_VIRTUAL_EL2='1', QEMU_HVF_VIRTUAL_SHADOW='1',
                   QEMU_HVF_VIRTUAL_LEDGER=str(ledger.resolve()))
    remote = None
    item = dict(accel=accel, case=case, iterations=count, command=cmd, passed=False)
    with (a.out / f'{name}.stderr').open('w') as log:
        child = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=log)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError('QEMU exited before GDB connection')
                try:
                    remote = Remote(port)
                    break
                except ConnectionRefusedError:
                    time.sleep(.02)
            assert remote is not None, 'GDB connection timeout'
            remote.sock.settimeout(30)
            remote.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            remote.command('?')
            def bp(address, enabled):
                assert remote.command(f'{"Z" if enabled else "z"}1,{address:x},4') == 'OK'
            def resume():
                remote.send('c')
                return remote.receive()
            def gprs():
                return struct.unpack_from('<33Q', bytes.fromhex(remote.command('g')))
            bp(PC, True)
            resume()
            before = registers(remote)
            assert before['pc'] == PC and before['SPRR_CONFIG_EL2'] == 0
            assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
            hashes = {hex(p): hashlib.sha256(read_phys(remote, p, 0x4000)).hexdigest()
                      for p in TABLES}
            for off in range(0, len(payload), 512):
                block = payload[off:off + 512]
                assert remote.command(f'M{PA + off:x},{len(block):x}:' + block.hex()) == 'OK'
            assert read_phys(remote, PA, len(payload)) == payload
            # A breakpoint on the loop page forces TCG to execute one
            # instruction per TB (accel/tcg/cpu-exec.c:check_for_breakpoints).
            # Branch out of the 16 KiB page before stopping either backend.
            assert remote.command(f'M{STOP_PA:x},4:00000014') == 'OK'
            stop_hash = hashlib.sha256(read_phys(remote, STOP_PA, 0x4000)).hexdigest()
            code_hash = hashlib.sha256(read_phys(remote, PA & ~0x3fff, 0x4000)).hexdigest()
            assert remote.command('Qqemu.PhyMemMode:0') == 'OK'
            assert remote.command('P21=' + (0xa00003c8).to_bytes(4, 'little').hex()) == 'OK'
            for reg, value in ((0, 1), (5, 0x40010), (6, 0xfb),
                               (9, before['SPRR_PPERM_EL2']), (10, PC + 0x300),
                               (12, 0x1234), (19, count), (20, case)):
                assert remote.command(f'P{reg:x}=' + value.to_bytes(8, 'little').hex()) == 'OK'
            bp(PC, False)
            bp(PC + 0x100, True)
            resume()
            ready = registers(remote)
            assert ready['pc'] == PC + 0x100, hex(ready['pc'])
            assert ready['GXF_CONFIG_EL2'] == 1 and ready['SPRR_CONFIG_EL2'] == 0xfb
            assert ready['CURRENTG'] == 0
            bp(PC + 0x100, False)
            bp(STOP_PC, True)
            start = time.monotonic_ns()
            resume()
            elapsed = (time.monotonic_ns() - start) / 1e9
            after = gprs()
            assert after[32] == STOP_PC, hex(after[32])
            assert after[19] == 0
            expected = count * (24 if case < 2 else 0x1234 if case == 2 else 1)
            assert after[9] == expected, (after[9], expected)
            state = registers(remote)
            assert state['CURRENTG'] == 0
            assert state['SPRR_CONFIG_EL2'] == ready['SPRR_CONFIG_EL2']
            assert state['SPRR_PPERM_EL2'] == ready['SPRR_PPERM_EL2']
            assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
            assert hashes == {hex(p): hashlib.sha256(read_phys(remote, p, 0x4000)).hexdigest()
                              for p in TABLES}
            assert code_hash == hashlib.sha256(read_phys(remote, PA & ~0x3fff, 0x4000)).hexdigest()
            assert stop_hash == hashlib.sha256(read_phys(remote, STOP_PA, 0x4000)).hexdigest()
            item.update(passed=True, host_seconds=elapsed, checksum=hex(after[9]),
                        table_hashes=hashes, code_page_sha256=code_hash)
        except Exception as exc:
            item['error'] = repr(exc)
        finally:
            if remote:
                remote.sock.close()
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            item['process_returncode'] = child.returncode
    item['diagnostic_bytes'] = (a.out / f'{name}.stderr').stat().st_size
    return item


def main():
    signal.signal(signal.SIGTERM, terminate)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--dtree', type=Path, required=True)
    ap.add_argument('--sptm', type=Path, required=True)
    ap.add_argument('--ledger', type=Path, required=True)
    ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
    ap.add_argument('--repeat', type=int, default=5)
    ap.add_argument('--compute-count', type=int, default=100000000)
    ap.add_argument('--read-count', type=int, default=10000)
    ap.add_argument('--transition-count', type=int, default=1000)
    a = ap.parse_args()
    if not 1 <= a.repeat <= 10 or not 1 <= a.compute_count <= 100000000:
        ap.error('repeat/compute count out of bounds')
    if not 1 <= a.read_count <= 100000 or not 1 <= a.transition_count <= 10000:
        ap.error('trap count out of bounds')
    a.out.mkdir(exist_ok=False)
    raw = assemble(a.out, Path(__file__).with_suffix('.S'))
    struct.pack_into('<I', raw, 0x200,
                     0x14000000 | (((STOP_PC - (PC + 0x200)) // 4) & 0x3ffffff))
    data = a.ledger.read_bytes()
    assert data[:4] == b'DVEL' and len(data) == 8 + 4 * struct.unpack_from('<I', data, 4)[0]
    words = list(struct.unpack_from('<' + str((len(data) - 8) // 4) + 'I', data, 8))
    adapted = bytearray(raw)
    for off in range(0, len(raw), 4):
        word = struct.unpack_from('<I', raw, off)[0]
        if ((word & 0xffc00000 == 0xd5000000 and (word >> 19) & 3)
                or word in (0x00201420, 0x00201400)):
            if word not in words:
                words.append(word)
            assert len(words) <= 4096
            struct.pack_into('<I', adapted, off, 0xd4000003 | ((0xe000 + words.index(word)) << 5))
    ledger = a.out / 'fixture.ledger'
    ledger.write_bytes(b'DVEL' + struct.pack('<I', len(words)) + struct.pack('<' + str(len(words)) + 'I', *words))
    (a.out / 'raw.bin').write_bytes(raw)
    (a.out / 'adapted.bin').write_bytes(adapted)
    report = dict(qemu_sha256=hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
                  sptm_sha256=hashlib.sha256(a.sptm.read_bytes()).hexdigest(),
                  raw_sha256=hashlib.sha256(raw).hexdigest(),
                  adapted_sha256=hashlib.sha256(adapted).hexdigest(),
                  stop_pc=hex(STOP_PC), loop_pc=hex(PC + 0x100),
                  host_load=os.getloadavg(), runs=[], medians={})
    cases = [('measurement_floor', 0, 0), ('integer', 0, a.compute_count),
             ('neon', 1, a.compute_count), ('tpidr_read', 2, a.read_count),
             ('genter_gexit', 3, a.transition_count)]
    def save():
        (a.out / 'results.json').write_text(json.dumps(report, indent=2))
    for rep in range(a.repeat):
        for label, case, count in cases:
            for accel in (('hvf', 'tcg') if rep % 2 == 0 else ('tcg', 'hvf')):
                item = run(a, accel, case, count, f'{rep}_{label}_{accel}',
                           adapted if accel == 'hvf' else raw, ledger)
                item.update(label=label, repetition=rep)
                report['runs'].append(item)
                save()
                print(json.dumps({k: v for k, v in item.items() if k not in ('command', 'table_hashes')}), flush=True)
                if not item['passed']:
                    return 1
    for label, _, count in cases:
        med = {accel: statistics.median(r['host_seconds'] for r in report['runs']
                                       if r['label'] == label and r['accel'] == accel)
               for accel in ('hvf', 'tcg')}
        med['tcg_over_hvf'] = med['tcg'] / med['hvf']
        med['iterations'] = count
        report['medians'][label] = med
    save()
    print(json.dumps(report['medians'], indent=2), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
