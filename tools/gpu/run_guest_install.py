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
    a = p.parse_args()
    if not SAFE_TAG.fullmatch(a.tag) or len(a.tag) > 40:
        p.error('invalid tag')
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
    # The restore shell owns its console; native HID pings belong to system boots.
    env.update(DVM_QEMU=m['qemu_argv'][0], DARWIN_INPUT_UART='0',
        DARWIN_TOUCH_EVENTS=str(out/'events.jsonl'))
    serial, uart, stop = out/f'{a.tag}.serial.log', out/'uart.sock', out/'stop'
    argv = ['bash', str(repo/'tools/probe.sh'), '--tag', a.tag, '--out', str(out),
        '--dtree', m['qemu_argv'][m['qemu_argv'].index('-dtree')+1],
        '--bootkc', m['qemu_argv'][m['qemu_argv'].index('-bootkc')+1],
        '--secs', '120', '--mem', '12G', '--ramdisk', str(a.stage/'ramdisk.dmg'),
        '--uart-socket', str(uart), '--stop-file', str(stop),
        '--pid-file', str(out/'qemu.pid'), '--launch-manifest', str(out/'launch.json'),
        '--', '-drive', f'if=none,id=ans,file={disk},format=qcow2',
        '-smp', '6', '-accel', 'tcg,thread=multi',
        '-fb', m['qemu_argv'][m['qemu_argv'].index('-fb')+1],
        '-fbmode', m['qemu_argv'][m['qemu_argv'].index('-fbmode')+1]]
    (out/'orchestration.json').write_text(json.dumps(dict(argv=argv, manifest=str(a.manifest),
        installer='sh /libexec/gpu-load-install.sh'), indent=2)+'\n')
    with (out/'probe.txt').open('w') as log:
        proc = subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT)
    connected = None
    sent = False
    try:
        deadline = time.monotonic()+150
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
    derived = {key:m[key] for key in ('qemu_argv','qemu_inputs','qemu_env')}
    derived.update(format='darwin-vm-warm-disk-v1', created_unix=time.time(), source_manifest=str(a.manifest.resolve()))
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
