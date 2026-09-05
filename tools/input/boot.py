#!/usr/bin/env python3
"""Cold-start a migrated disk with the input daemon, retaining a display launch.

The template may be a restore report; incoming RAM state is removed explicitly
because a new helper trust-cache entry must be loaded at boot. The supplied
disk is an installed, idle disposable child, never an immutable checkpoint.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template', type=Path, required=True)
    parser.add_argument('--disk', type=Path, required=True)
    parser.add_argument('--tc', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--gdb-port', type=int, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', args.tag):
        parser.error('invalid tag')
    out = Path('/tmp/dvm')/args.tag
    out.mkdir(exist_ok=False)
    source = json.loads(args.template.read_text())
    argv, command, i = source['argv'], [], 0
    replacements = {
        '-drive': f'if=none,id=ans,file={args.disk.resolve()},format=qcow2',
        '-tc': str(args.tc.resolve()),
        '-display': 'cocoa,zoom-to-fit=on',
    }
    while i < len(argv):
        key = argv[i]
        if key in ('-incoming', '-loadvm', '-monitor', '-qmp', '-gdb', '-chardev', '-serial'):
            i += 2
        elif key == '-S':
            i += 1
        elif key in replacements:
            command += [key, replacements[key]]
            i += 2
        else:
            command.append(key)
            i += 1
    command += ['-S', '-monitor', f'unix:{out}/monitor.sock,server=on,wait=off',
                '-qmp', f'unix:{out}/qmp.sock,server=on,wait=off',
                '-gdb', f'tcp:127.0.0.1:{args.gdb_port}',
                '-chardev', f'socket,id=input_uart,path={out}/uart.sock,server=on,wait=off,logfile={out}/serial.log',
                '-serial', 'chardev:input_uart']
    env = {k:v for k,v in os.environ.items() if not k.startswith(('DARWIN_', 'GXFSTAT_'))}
    model_env = dict(source.get('qemu_env', source.get('env', {})))
    model_env['DARWIN_TOUCH_EVENTS'] = str(out/'events.jsonl')
    # Full RPC dumps are unnecessary for latency testing; errors stay logged.
    model_env['DARWIN_DCP_IOMFB_RPC_TRACE'] = '0'
    env.update(model_env)
    with open(out/'stderr.log', 'w') as log:
        process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)
    (out/'qemu.pid').write_text(str(process.pid))
    (out/'launch.json').write_text(json.dumps(dict(format='darwin-vm-qemu-launch-v1',
        argv=command, env=model_env), indent=2)+'\n')
    print(json.dumps(dict(pid=process.pid, output=str(out), paused=True)))


if __name__ == '__main__':
    main()
