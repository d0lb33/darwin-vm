#!/usr/bin/env python3
"""Stage a one-shot native bitmap probe in a small restore ramdisk.

Only copied caches/artifacts are changed here. The restore guest installs the
probe into a fresh disk child; the original native-input service is retained.
"""
import argparse
from pathlib import Path
import plistlib
import shutil
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', type=Path, required=True)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--system-tc', type=Path, required=True)
    p.add_argument('--probe-tc', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--power-binary', type=Path)
    p.add_argument('--power-tc', type=Path)
    p.add_argument('--activation-binary', type=Path)
    p.add_argument('--activation-tc', type=Path)
    p.add_argument('--power-service-binary', type=Path)
    p.add_argument('--power-service-tc', type=Path)
    a = p.parse_args()
    if bool(a.power_binary) != bool(a.power_tc):
        p.error('--power-binary and --power-tc must be supplied together')
    if bool(a.activation_binary) != bool(a.activation_tc):
        p.error('--activation-binary and --activation-tc must be supplied together')
    if bool(a.power_service_binary) != bool(a.power_service_tc):
        p.error('--power-service-binary and --power-service-tc must be supplied together')
    source = a.cache.read_bytes()
    cache = plistlib.loads(source)
    service_path = '/System/Library/LaunchDaemons/com.apple.dvm-graphics-probe.plist'
    if service_path in cache['LaunchDaemons']:
        p.error('probe already registered; use a fresh source cache')
    service = dict(Label='com.apple.dvm-graphics-probe',
        ProgramArguments=['/usr/local/libexec/dvm-graphics-probe'],
        RunAtLoad=True, LaunchOnlyOnce=True, UserName='root', ProcessType='Interactive',
        StandardOutputPath='/dev/console', StandardErrorPath='/dev/console')
    cache['LaunchDaemons'][service_path] = service
    extra_services = []
    for name, binary, tc, persistent in [
        ('power-rtc-probe', a.power_binary, a.power_tc, False),
        ('activation-probe', a.activation_binary, a.activation_tc, False),
        ('power-pv-service', a.power_service_binary, a.power_service_tc, True),
    ]:
        if not binary:
            continue
        extra = dict(service, Label=f'com.apple.dvm-{name}',
            ProgramArguments=[f'/usr/local/libexec/dvm-{name}'])
        if persistent:
            extra.pop('LaunchOnlyOnce')
            extra.update(KeepAlive=True, ThrottleInterval=30)
        path = f'/System/Library/LaunchDaemons/com.apple.dvm-{name}.plist'
        if path in cache['LaunchDaemons']:
            p.error(f'{name} already registered')
        cache['LaunchDaemons'][path] = extra
        extra_services.append((name, binary, tc, extra))
    encoded = plistlib.dumps(cache, fmt=plistlib.FMT_BINARY, sort_keys=False)
    assert plistlib.loads(encoded) == cache
    a.output.mkdir(exist_ok=False)
    repo = Path(__file__).resolve().parents[2]
    image = a.output/'ramdisk.dmg'
    shutil.copyfile(repo/'firmware/ramdisk.dmg', image)
    wrapper = repo/'tools/rootfs/safe_attach.sh'
    mount = Path(subprocess.check_output([str(wrapper), 'attach', str(image), '--owners', 'on'], text=True).strip())
    try:
        d = mount/'libexec'
        shutil.copyfile(a.binary, d/'dvm-graphics-probe')
        (d/'dvm-graphics-probe').chmod(0o755)
        (d/'dvm-graphics-before.plist').write_bytes(source)
        (d/'dvm-graphics-cache.plist').write_bytes(encoded)
        (d/'dvm-graphics-service.plist').write_bytes(plistlib.dumps(service))
        for name, binary, tc, extra in extra_services:
            target = d/f'dvm-{name}'
            shutil.copyfile(binary, target)
            target.chmod(0o755)
            (d/f'dvm-{name}-service.plist').write_bytes(plistlib.dumps(extra))
        shutil.copyfile(Path(__file__).with_name('install_graphics_probe.sh'), d/'dvm-graphics-install.sh')
        subprocess.run(['sync'], check=True)
    finally:
        subprocess.run([str(wrapper), 'detach', str(mount)], check=True)
    subprocess.run(['python3', str(repo/'tools/rootfs/merge_tc.py'), str(a.output/'system.tc'), str(a.system_tc), str(a.probe_tc)]
                   + [str(tc) for name, binary, tc, extra in extra_services], check=True)
    (a.output/'launchd.plist').write_bytes(encoded)


if __name__ == '__main__':
    main()
