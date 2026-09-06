#!/usr/bin/env python3
"""Boot the durable native-SMC baseline in a fresh disk child until interrupted."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import signal
import time

from checkpoint_common import atomic_json, sha256, verify_backing_chain
from warm_boot_probe import boot_command


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, default=Path.home() / 'dvm-artifacts/native-smc/default.json')
    p.add_argument('--display', default='cocoa,zoom-to-fit=on' if os.uname().sysname == 'Darwin' else 'sdl')
    p.add_argument('--vnc')
    p.add_argument('--fb')
    a = p.parse_args()
    manifest = json.loads(a.manifest.read_text())
    if manifest.get('battery_source') != 'emulated-smc':
        p.error('default manifest must describe an emulated-SMC disk')
    verify_backing_chain(manifest['disk']['backing_chain'])
    for name, expected in manifest['qemu_inputs'].items():
        if sha256(Path(name)) != expected['sha256']:
            p.error(f'boot input changed: {name}')
    out = Path('/tmp/dvm') / f'SMC_{time.time_ns()}'
    out.mkdir(parents=True)
    qemu_img = Path(manifest['qemu_argv'][0]).with_name('qemu-img')
    subprocess.run([str(qemu_img), 'create', '-f', 'qcow2', '-F', 'qcow2', '-b',
                    manifest['disk']['path'], str(out / 'disk.qcow2')], check=True)
    command = boot_command(manifest['qemu_argv'], out)
    command[command.index('-display') + 1] = a.display
    if a.vnc:
        command += ['-vnc', a.vnc]
    if a.fb:
        command[command.index('-fb') + 1] = a.fb
    model_env = dict(manifest['qemu_env'], DARWIN_TOUCH_EVENTS=str(out / 'events.jsonl'))
    if model_env.get('DARWIN_INPUT_UART', '0') != '0':
        model_env['DARWIN_INPUT_STATUS'] = str(out / 'input-status.json')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DARWIN_', 'DVM_', 'GXFSTAT_'))}
    # The initial battery state is the one supported interactive override.
    if 'DARWIN_SMC_BATTERY' in os.environ:
        model_env['DARWIN_SMC_BATTERY'] = os.environ['DARWIN_SMC_BATTERY']
    env.update(model_env)
    atomic_json(out / 'launch.json', dict(format='darwin-vm-qemu-launch-v1', argv=command, env=model_env))
    print(f'Native SMC disk boot; logs and writable child: {out}', flush=True)
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    proc = None
    try:
        with (out / 'stderr.log').open('wb') as log:
            proc = subprocess.Popen(command, env=env, stdout=log, stderr=log)
            (out / 'qemu.pid').write_text(str(proc.pid) + '\n')
            proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    if proc and proc.returncode not in (0, -2, -15):
        raise SystemExit(proc.returncode)


if __name__ == '__main__':
    main()
