#!/usr/bin/env python3
"""Package a built QEMU for a named, discoverable macOS VM test window.

Copies the already signed executable; never modifies an in-use QEMU build.
Launch the contained executable with the normal VM argv, not an empty launch.
"""
import argparse
from pathlib import Path
import plistlib
import shutil


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.out.exists() or a.out.suffix != '.app':
        p.error('output must be a new .app directory')
    binary = a.out / 'Contents/MacOS'
    binary.mkdir(parents=True)
    for name in ('qemu-system-aarch64', 'qemu-img'):
        shutil.copy2(a.qemu.with_name(name), binary / name)
    info = dict(CFBundleIdentifier='org.darwin-vm.touch-test',
                CFBundleName='Darwin VM Touch Test', CFBundleVersion='1',
                CFBundlePackageType='APPL', CFBundleExecutable='qemu-system-aarch64',
                NSHighResolutionCapable=True)
    (a.out/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    print(binary/'qemu-system-aarch64')


if __name__ == '__main__':
    main()
