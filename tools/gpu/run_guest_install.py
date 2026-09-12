#!/usr/bin/env python3
"""Install the load experiment through an owned restore VM and disposable disk.

The parent disk and all pinned firmware are verified before boot. This tool
never attaches System/Data to the host. probe.sh supplies bounded teardown.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import select
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import SAFE_TAG, atomic_json, qcow2_backing_chain, sha256, verify_backing_chain


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--tag', required=True)
    p.add_argument('--mmio-restore',action='store_true',help='supply owned RAM/registration for the three-range managed transport DT; no GPU worker or commands')
    p.add_argument('--install-seconds', type=int, default=120,
                   help='restore-shell and guarded-installer deadline (default: 120)')
    a = p.parse_args()
    if not 30 <= a.install_seconds <= 1800:
        p.error('--install-seconds must be between 30 and 1800')
    if not SAFE_TAG.fullmatch(a.tag) or len(a.tag) > 40:
        p.error('invalid tag')
    for name in ('ramdisk.dmg', 'system.tc'):
        if not (a.stage / name).is_file():
            p.error(f'missing staged input: {a.stage / name}')
    repo = Path(__file__).resolve().parents[2]
    out = Path('/tmp/dvm')/a.tag
    out.mkdir(exist_ok=False)
    m = json.loads(a.manifest.read_text())
    verify_backing_chain(m['disk']['backing_chain'])
    for name, entry in m['qemu_inputs'].items():
        if sha256(Path(name)) != entry['sha256']:
            raise RuntimeError(f'changed input: {name}')
    disk = out/'disk.qcow2'
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2',
        '-b', m['disk']['path'], str(disk)], check=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DARWIN_', 'GXFSTAT_', 'DVM_'))}
    env.update(m['qemu_env'])
    if a.mmio_restore:
        if any(k.startswith('DARWIN_GPU_') for k in env):
            raise ValueError('restore manifest carries an uncontrolled GPU backend')
        # The installed system's DT may already contain dvm-gpu-shm. QEMU
        # requires its paired RAM even though the restore shell never opens it.
        # Mode 1 is the idle local echo device; no host Metal worker is needed.
        shared=out/'shared-ram.bin'
        fd=os.open(shared,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600)
        try:os.ftruncate(fd,16*1024*1024)
        finally:os.close(fd)
        env['DARWIN_GPU_SHM_PATH']=str(shared)
        # This guest's three-range DT includes the kernel-only registration
        # aperture. Keep that exact layout and pair its DRAM mirror as well.
        managed=out/'managed-ram.bin'
        fd=os.open(managed,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600)
        try:os.ftruncate(fd,12*1024*1024*1024)
        finally:os.close(fd)
        env['DARWIN_GPU_MANAGED_RAM_PATH']=str(managed)
        env['DARWIN_GPU_MANAGED_PAGES_PATH']=str(out/'managed-pages.bin')
    # The restore shell owns its console; native HID pings belong to system boots.
    env.update(DVM_QEMU=m['qemu_argv'][0], DARWIN_INPUT_UART='0',
        DARWIN_TOUCH_EVENTS=str(out/'events.jsonl'))
    serial, uart, stop = out/f'{a.tag}.serial.log', out/'uart.sock', out/'stop'
    argv = ['bash', str(repo/'tools/probe.sh'), '--tag', a.tag, '--out', str(out),
        '--dtree', m['qemu_argv'][m['qemu_argv'].index('-dtree')+1],
        '--bootkc', m['qemu_argv'][m['qemu_argv'].index('-bootkc')+1],
        '--secs', str(a.install_seconds), '--mem', '12G', '--ramdisk', str(a.stage/'ramdisk.dmg'),
        '--uart-socket', str(uart), '--stop-file', str(stop),
        '--pid-file', str(out/'qemu.pid'), '--launch-manifest', str(out/'launch.json'),
        '--', '-drive', f'if=none,id=ans,file={disk},format=qcow2',
        '-smp', '6', '-accel', 'tcg,thread=multi',
        '-fb', m['qemu_argv'][m['qemu_argv'].index('-fb')+1],
        '-fbmode', m['qemu_argv'][m['qemu_argv'].index('-fbmode')+1]]
    if (a.stage/'restore.tc').exists():
        argv[2:2] = ['--tc', str((a.stage/'restore.tc').resolve())]
    (out/'orchestration.json').write_text(json.dumps(dict(argv=argv, manifest=str(a.manifest),
        installer='sh /libexec/gpu-load-install.sh'), indent=2)+'\n')
    with (out/'probe.txt').open('w') as log:
        proc = subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT)
    connected = None
    sent = False
    try:
        deadline = time.monotonic()+a.install_seconds+30
        while proc.poll() is None and time.monotonic() < deadline:
            data = serial.read_bytes() if serial.exists() else b''
            if not sent and b"can't access tty" in data:
                connected = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                connected.settimeout(5)
                connected.connect(str(uart))
                connected.sendall(b'sh /libexec/gpu-load-install.sh\n')
                sent = True
                print('restore shell ready; sent guarded installer', flush=True)
            if b'GPU_LOAD_INSTALLED\r' in data or b'GPU_LOAD_INSTALLED\n' in data:
                stop.write_text('verified GPU_LOAD_INSTALLED marker\n')
            if connected and select.select([connected], [], [], 0)[0]:
                # Drain UART output; otherwise verbose helpers block on the socket
                # even though QEMU also records their bytes in the serial log.
                connected.recv(1024 * 1024)
            time.sleep(.2)
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=20)
    finally:
        if connected:
            connected.close()
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=20)
    print((out/'probe.txt').read_text(), flush=True)
    if not stop.exists() or proc.returncode:
        raise RuntimeError(f'install did not complete; inspect {out}')
    disk.chmod(0o444)
    verify_backing_chain(m['disk']['backing_chain'])
    derived = {key:m[key] for key in ('qemu_argv','qemu_inputs','qemu_env')}
    derived.update(format='darwin-vm-warm-disk-v1', created_unix=time.time(), source_manifest=str(a.manifest.resolve()))
    for key in ('battery_source', 'driver_smc_migration',
                'cellular_service_installation', 'cellular_boot_validation',
                'tcg_comparison', 'input_installation'):
        if key in m:
            derived[key] = m[key]
    if (a.stage/'provenance.json').exists():
        installation = json.loads((a.stage/'provenance.json').read_text())
        history = list(m.get('installation_history') or [])
        if installation.get('scope', '').startswith('restore reviewed native HID'):
            if m.get('guest_installation'):
                derived['guest_installation'] = m['guest_installation']
            derived['input_installation'] = installation
        else:
            if m.get('guest_installation'):
                history.append(m['guest_installation'])
            derived['guest_installation'] = installation
        if history:
            derived['installation_history'] = history
    derived['disk'] = dict(path=str(disk.resolve()),
        backing_chain=qcow2_backing_chain(Path(shutil.which('qemu-img')),disk))
    normal = derived['qemu_argv']
    normal[normal.index('-drive')+1] = f'if=none,id=ans,file={disk.resolve()},format=qcow2'
    tc = (a.stage/'system.tc').resolve()
    old = Path(normal[normal.index('-tc')+1]).resolve()
    for key in list(derived['qemu_inputs']):
        if Path(key).resolve()==old:
            del derived['qemu_inputs'][key]
    normal[normal.index('-tc')+1] = str(tc)
    derived['qemu_inputs'][str(tc)] = dict(bytes=tc.stat().st_size,sha256=sha256(tc))
    atomic_json(out/'warm-manifest.json',derived)
    print(f'installed parent stopped and read-only: {disk}', flush=True)


if __name__ == '__main__':
    main()
