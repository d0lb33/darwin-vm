#!/usr/bin/env python3
"""Turn a virtual-battery System disk into a native-battery candidate.

Given an immutable qcow2 whose System volume carries the powerd null guard and
the dvm-power-pv-service publisher (the 2026-09-05 working chain), stage a
guarded restore-ramdisk installer that, after checking every preimage:

  * restores the original 24A5430a powerd (its CDHash is already in the
    system trust cache: it is a stock binary);
  * rewrites /System/Library/xpc/launchd.plist without the publisher job;
  * removes the publisher's LaunchDaemon plist and executable.

Nothing else on the System volume is touched; the input helper, graphics and
activation probes and the software-display cache edits stay.  The emulated SMC
battery (qemu darwin_smc.c, dt_fixup -enable smc) then is the only internal
power source, and the original powerd is what consumes it.  See
docs/re/native-battery-smc.md.

The child disk is produced by tools/re/install_staged_helpers.py from a
restore-launch template; only the ramdisk, ANS child and sockets differ.
"""
import argparse
import hashlib
import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
POWERD = '/System/Library/CoreServices/powerd.bundle/powerd'
LAUNCHD = '/System/Library/xpc/launchd.plist'
PV_PLIST = '/System/Library/LaunchDaemons/com.apple.dvm-power-pv-service.plist'
PV_BIN = '/usr/local/libexec/dvm-power-pv-service'
INSTALLER = '/libexec/dvm-native-battery-install.sh'
MARKER = 'DVM_NATIVE_BATTERY_INSTALLED'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def launchd_without_publisher(before):
    cache = plistlib.loads(before.read_bytes())
    daemons = cache['LaunchDaemons']
    if PV_PLIST not in daemons:
        raise ValueError('launchd cache does not carry the publisher job')
    if daemons[PV_PLIST].get('Label') != 'com.apple.dvm-power-pv-service':
        raise ValueError('unexpected publisher job label')
    del daemons[PV_PLIST]
    return plistlib.dumps(cache, fmt=plistlib.FMT_BINARY, sort_keys=False)


def installer_script():
    q = shlex.quote
    lines = ['#!/bin/sh', 'set -eu', 'mount_apfs /dev/disk1s1 /mnt1', 'root=/mnt1']
    for target, before in [(POWERD, 'nb-powerd-before'), (LAUNCHD, 'nb-launchd-before')]:
        lines.append(f'test "$(cksum < {q("/mnt1" + target)})" = "$(cksum < /libexec/{before})"')
    lines += [f'test -f {q("/mnt1" + PV_PLIST)}', f'test -f {q("/mnt1" + PV_BIN)}',
              'echo DVM_NATIVE_BATTERY_PREIMAGES_VERIFIED']
    for target, after in [(POWERD, 'nb-powerd-after'), (LAUNCHD, 'nb-launchd-after')]:
        t = q('/mnt1' + target)
        lines += [f'cp /libexec/{after} {t}', f'chown 0:0 {t}',
                  f'test "$(cksum < {t})" = "$(cksum < /libexec/{after})"']
    lines += [f'rm {q("/mnt1" + PV_PLIST)}', f'rm {q("/mnt1" + PV_BIN)}',
              f'test ! -e {q("/mnt1" + PV_PLIST)}', f'test ! -e {q("/mnt1" + PV_BIN)}',
              'sync', f'echo {MARKER}']
    return '\n'.join(lines) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--parent', type=Path, required=True, help='read-only virtual-battery disk')
    p.add_argument('--powerd-before', type=Path, required=True, help='the guarded powerd now on the disk')
    p.add_argument('--powerd-after', type=Path, required=True, help='original 24A5430a powerd')
    p.add_argument('--launchd-before', type=Path, required=True, help='launchd cache now on the disk')
    p.add_argument('--template', type=Path, required=True, help='restore launch JSON to derive from')
    p.add_argument('--qemu', type=Path, default=REPO / 'qemu-sptm/build/qemu-system-aarch64')
    p.add_argument('--dtree', type=Path, required=True, help='restore device tree for the installer boot')
    p.add_argument('--ramdisk-base', type=Path, default=REPO / 'firmware/ramdisk.dmg')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--tag', required=True)
    p.add_argument('--timeout', type=int, default=180)
    a = p.parse_args()
    if a.out.exists():
        p.error('--out must be new')
    if a.parent.stat().st_mode & 0o222:
        p.error('--parent must be read-only')
    if sha256(a.powerd_before) == sha256(a.powerd_after):
        p.error('before and after powerd are identical')
    a.out.mkdir(parents=True)
    payload = a.out / 'payload'
    payload.mkdir()
    shutil.copyfile(a.powerd_before, payload / 'nb-powerd-before')
    shutil.copyfile(a.powerd_after, payload / 'nb-powerd-after')
    shutil.copyfile(a.launchd_before, payload / 'nb-launchd-before')
    (payload / 'nb-launchd-after').write_bytes(launchd_without_publisher(a.launchd_before))
    (payload / INSTALLER.split('/')[-1]).write_text(installer_script())
    subprocess.run(['bash', '-n', str(payload / INSTALLER.split('/')[-1])], check=True)

    image = a.out / 'installer.dmg'
    shutil.copyfile(a.ramdisk_base, image)
    wrapper = REPO / 'tools/rootfs/safe_attach.sh'
    mount = Path(subprocess.check_output([str(wrapper), 'attach', str(image), '--owners', 'on'], text=True).strip())
    try:
        for f in payload.iterdir():
            shutil.copyfile(f, mount / 'libexec' / f.name)
        subprocess.run(['sync'], check=True)
    finally:
        subprocess.run([str(wrapper), 'detach', str(mount)], check=True)

    base = json.loads(a.template.read_text())
    argv = list(base['argv'])
    argv[0] = str(a.qemu.resolve())
    argv[argv.index('-dtree') + 1] = str(a.dtree.resolve())
    for key in ('-sptm', '-txm'):
        argv[argv.index(key) + 1] = str((REPO / 'firmware' / key[1:]).resolve())
    argv[argv.index('-bootkc') + 1] = str((REPO / 'firmware/bootkc').resolve())
    if '-display' in argv:
        argv[argv.index('-display') + 1] = 'none'
    # install_staged_helpers requires the template's ANS drive to be the parent.
    argv[argv.index('-drive') + 1] = f'if=none,id=ans,file={a.parent.resolve()},format=qcow2'
    template = a.out / 'restore-launch.json'
    template.write_text(json.dumps(dict(format='darwin-vm-qemu-launch-v1', argv=argv,
                                        env={'DARWIN_RTC_PV': '0'}), indent=1))
    (a.out / 'inputs.json').write_text(json.dumps({
        str(x): sha256(x) for x in (a.parent, a.powerd_before, a.powerd_after, a.launchd_before, a.qemu, a.dtree)}, indent=1))
    env = dict(os.environ, PATH=str(a.qemu.resolve().parent) + os.pathsep + os.environ.get('PATH', ''))
    subprocess.run([sys.executable, str(REPO / 'tools/re/install_staged_helpers.py'),
                    '--template', str(template), '--parent', str(a.parent), '--ramdisk', str(image),
                    '--out', str(a.out / 'install'), '--tag', a.tag, '--timeout', str(a.timeout),
                    '--installer', INSTALLER, '--install-marker', MARKER], check=True, env=env)
    print('candidate disk:', a.out / 'install/disk.qcow2')


if __name__ == '__main__':
    main()
