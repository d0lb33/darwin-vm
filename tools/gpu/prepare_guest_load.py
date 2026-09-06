#!/usr/bin/env python3
"""Stage signed load probe in a copied small ramdisk; never mount System/Data."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build', type=Path, required=True)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--system-tc', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--mode', choices=('load', 'forward'), default='load')
    p.add_argument('--surface-only', action='store_true', help='forward mode: independent local IOSurface check')
    p.add_argument('--interactive-load', action='store_true',
        help='load mode: use the original input helper ProcessType for a scheduling control')
    p.add_argument('--memory-limit-mb', type=int,
        help='load mode: bounded JetsamMemoryLimit for this helper only (16–256 MiB)')
    a = p.parse_args()
    if a.surface_only and a.mode!='forward':
        p.error('--surface-only requires --mode forward')
    if a.interactive_load and a.mode!='load':
        p.error('--interactive-load requires --mode load')
    if a.memory_limit_mb is not None and (a.mode!='load' or not 16<=a.memory_limit_mb<=256):
        p.error('--memory-limit-mb requires load mode and 16–256 MiB')
    repo = Path(__file__).resolve().parents[2]
    cache_bytes = a.cache.read_bytes()
    cache = plistlib.loads(cache_bytes)
    service_path = '/System/Library/LaunchDaemons/org.darwin-vm.gpu-load.plist'
    if service_path in cache['LaunchDaemons']:
        p.error('probe already registered; choose the original parent cache')
    service = dict(Label='org.darwin-vm.gpu-load',
        ProgramArguments=['/usr/local/libexec/dvm-gpu-load'],
        RunAtLoad=True, LaunchOnlyOnce=True, UserName='root',
        StandardOutputPath='/dev/console', StandardErrorPath='/dev/console')
    if a.interactive_load:
        service['ProcessType']='Interactive'
    if a.memory_limit_mb is not None:
        service['JetsamProperties']={'JetsamMemoryLimit':a.memory_limit_mb}
    if a.mode == 'forward':
        matches = [(key, value) for key, value in cache['LaunchDaemons'].items()
            if (value.get('ProgramArguments') or [None])[0] == '/usr/local/libexec/dvm-input']
        if len(matches) != 1:
            p.error('expected exactly one original input service')
        service_path, service = matches[0]
        service['ProgramArguments'] = ['/usr/local/libexec/dvm-gpu-transport']
        if a.surface_only:
            service.setdefault('EnvironmentVariables',{})['DVM_PROXY_SURFACE_ONLY']='1'
    cache['LaunchDaemons'][service_path] = service
    encoded = plistlib.dumps(cache, fmt=plistlib.FMT_BINARY, sort_keys=False)
    a.output.mkdir(exist_ok=False)
    image = a.output/'ramdisk.dmg'
    shutil.copyfile(repo/'firmware/ramdisk.dmg', image)
    wrapper = repo/'tools/rootfs/safe_attach.sh'
    mount = Path(subprocess.check_output([str(wrapper), 'attach', str(image), '--owners', 'on'], text=True).strip())
    try:
        d = mount/'libexec'
        names = ['dvm-gpu-load'] if a.mode == 'load' else ['dvm-gpu-work', 'dvm-gpu-transport']
        bundle = 'DVMProxy' if a.mode == 'load' else 'DVMForward'
        for name in names:
            shutil.copyfile(a.build/name, d/name)
            (d/name).chmod(0o755)
        shutil.copytree(a.build/f'{bundle}.bundle', d/f'{bundle}.bundle')
        (d/f'{bundle}.bundle/{bundle}').chmod(0o755)
        (d/'gpu-load-before.plist').write_bytes(cache_bytes)
        (d/'gpu-load-cache.plist').write_bytes(encoded)
        (d/'gpu-load-service.plist').write_bytes(plistlib.dumps(service))
        (d/'gpu-load-service-path.txt').write_text(service_path+'\n')
        installer = 'install_guest_load.sh' if a.mode == 'load' else 'install_guest_forward.sh'
        shutil.copyfile(Path(__file__).with_name(installer), d/'gpu-load-install.sh')
        subprocess.run(['sync'], check=True)
    finally:
        subprocess.run([str(wrapper), 'detach', str(mount)], check=True)
    subprocess.run(['python3', str(repo/'tools/rootfs/merge_tc.py'), str(a.output/'system.tc'),
        str(a.system_tc), str(a.build/'helper.tc')], check=True)
    (a.output/'launchd.plist').write_bytes(encoded)
    inputs={str(f.resolve()):hashlib.sha256(f.read_bytes()).hexdigest()
        for f in a.build.rglob('*') if f.is_file() and
        (f.suffix in ('.m','.h','.sh','.plist') or f.name in ('dvm-gpu-load','DVMProxy','DVMForward','dvm-gpu-work','dvm-gpu-transport','helper.tc'))}
    (a.output/'provenance.json').write_text(json.dumps(dict(build=str(a.build.resolve()),
        inputs=inputs,service=service,cache_sha256=hashlib.sha256(encoded).hexdigest()),indent=2)+'\n')


if __name__ == '__main__':
    main()
