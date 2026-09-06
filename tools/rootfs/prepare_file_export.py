#!/usr/bin/env python3
"""Prepare a small restore image that exports one System file over serial."""
import argparse
from pathlib import Path
import shlex
import shutil
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('guest_path')
    p.add_argument('output', type=Path)
    a = p.parse_args()
    if not a.guest_path.startswith('/mnt1/') or '..' in Path(a.guest_path).parts:
        p.error('guest path must be inside the mounted System volume /mnt1')
    if a.output.exists():
        p.error('output must be new')
    a.output.mkdir()
    repo = Path(__file__).resolve().parents[2]
    binary = a.output / 'dvm-file-export'
    sdk = subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip()
    subprocess.run(['clang', '-target', 'arm64-apple-ios7.0', '-isysroot', sdk,
                    '-Os', '-Wall', '-Wextra', '-Werror', '-Wno-incompatible-sysroot',
                    str(Path(__file__).with_name('export_guest_file.c')), '-o', str(binary)], check=True)
    subprocess.run(['codesign', '--force', '--sign', '-', '--timestamp=none', str(binary)], check=True)
    info = subprocess.check_output(['codesign', '-d', '--verbose=4', str(binary)], stderr=subprocess.STDOUT, text=True)
    cdhash = next(line.split('=', 1)[1] for line in info.splitlines() if line.startswith('CDHash='))
    (a.output / 'hashes.txt').write_text(cdhash + '\n')
    subprocess.run(['python3', str(repo / 'build_tc.py'), str(a.output / 'hashes.txt'), str(a.output / 'helper.tc')], check=True)
    subprocess.run(['python3', str(repo / 'tools/rootfs/merge_tc.py'), str(a.output / 'restore.tc'), str(repo / 'firmware/ramdisk.tc'), str(a.output / 'helper.tc')], check=True)
    image = a.output / 'ramdisk.dmg'
    shutil.copyfile(repo / 'firmware/ramdisk.dmg', image)
    wrapper = repo / 'tools/rootfs/safe_attach.sh'
    mount = Path(subprocess.check_output([str(wrapper), 'attach', str(image), '--owners', 'on'], text=True).strip())
    try:
        shutil.copyfile(binary, mount / 'libexec/dvm-file-export')
        (mount / 'libexec/dvm-file-export').chmod(0o755)
        (mount / 'libexec/dvm-file-export.sh').write_text(
            '#!/bin/sh\nset -eu\nmount_apfs -o ro /dev/disk1s1 /mnt1\n'
            '/libexec/dvm-file-export ' + shlex.quote(a.guest_path) + '\n'
            'echo DVM_FILE_EXPORTED\n')
        subprocess.run(['sync'], check=True)
    finally:
        subprocess.run([str(wrapper), 'detach', str(mount)], check=True)


if __name__ == '__main__':
    main()
