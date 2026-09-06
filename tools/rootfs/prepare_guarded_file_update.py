#!/usr/bin/env python3
"""Stage signed executable replacements for a disposable System disk child.

Spec: [{path: '/System/.../executable', before: '/host/original',
        after: '/host/replacement'}]. Checks all preimages before any rename.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('spec', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--tc', required=True, type=Path)
    a = p.parse_args()
    if a.output.exists():
        p.error('output must be new')
    entries = json.loads(a.spec.read_text())
    if not isinstance(entries, list) or not 1 <= len(entries) <= 16:
        p.error('requires 1..16 executable replacements')
    paths, hashes = set(), []
    for e in entries:
        path = e['path']
        if not path.startswith(('/System/', '/usr/')) or '..' in Path(path).parts or path in paths:
            p.error('paths must be unique, absolute System/usr executable paths')
        paths.add(path)
        for key in ('before', 'after'):
            binary = Path(e[key])
            if not binary.is_file() or binary.stat().st_size > 16 * 1024 * 1024:
                p.error('requires a regular executable of at most 16 MiB')
            subprocess.run(['codesign', '--verify', '--strict', str(binary)], check=True)
            e[key + '_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
        info = subprocess.check_output(['codesign', '-d', '--verbose=4', e['after']], stderr=subprocess.STDOUT, text=True)
        e['cdhash'] = re.search(r'^CDHash=([0-9a-f]{40})$', info, re.M).group(1)
        hashes.append(e['cdhash'])
    a.output.mkdir()
    repo = Path(__file__).resolve().parents[2]
    (a.output / 'updates.json').write_text(json.dumps(entries, indent=2) + '\n')
    (a.output / 'hashes.txt').write_text('\n'.join(hashes) + '\n')
    subprocess.run(['python3', str(repo / 'build_tc.py'), str(a.output / 'hashes.txt'), str(a.output / 'updates.tc')], check=True)
    subprocess.run(['python3', str(repo / 'tools/rootfs/merge_tc.py'), str(a.output / 'system.tc'), str(a.tc), str(a.output / 'updates.tc')], check=True)
    image = a.output / 'ramdisk.dmg'
    shutil.copyfile(repo / 'firmware/ramdisk.dmg', image)
    wrapper = repo / 'tools/rootfs/safe_attach.sh'
    mount = Path(subprocess.check_output([str(wrapper), 'attach', str(image), '--owners', 'on'], text=True).strip())
    try:
        script = ['#!/bin/sh', 'set -eu', 'mount_apfs /dev/disk1s1 /mnt1']
        # These CRC/length checks catch accidental mismatches. The caller pins
        # the ramdisk and immutable disk lineage by SHA-256 before booting.
        for i, e in enumerate(entries):
            target = shlex.quote('/mnt1' + e['path'])
            script += [f'test -f {target}', f'test ! -e {target}.dvm-new',
                       f'test "$(cksum < {target})" = "$(cksum < /libexec/update-{i}-before)"']
            for key in ('before', 'after'):
                shutil.copyfile(e[key], mount / f'libexec/update-{i}-{key}')
        for i, e in enumerate(entries):
            target = shlex.quote('/mnt1' + e['path'])
            script += [f'cp /libexec/update-{i}-after {target}.dvm-new',
                       f'chmod 755 {target}.dvm-new', f'chown 0:0 {target}.dvm-new',
                       f'test "$(cksum < {target}.dvm-new)" = "$(cksum < /libexec/update-{i}-after)"',
                       f'mv {target}.dvm-new {target}',
                       f'test "$(cksum < {target})" = "$(cksum < /libexec/update-{i}-after)"']
        script += ['sync', 'echo DVM_FILES_UPDATED']
        (mount / 'libexec/dvm-files-update.sh').write_text('\n'.join(script) + '\n')
        subprocess.run(['sync'], check=True)
    finally:
        subprocess.run([str(wrapper), 'detach', str(mount)], check=True)


if __name__ == '__main__':
    main()
