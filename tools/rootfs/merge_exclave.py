#!/usr/bin/env python3
"""Install the matching ExclaveOS payload on an offline image's Preboot volume.

SILManager on the AP reads its manifests below /private/preboot/Cryptexes/
ExclaveOS, even without secure-world execution. See docs/re/exclave-assets.md.
This never modifies the source image, replaces an existing tree, or touches
Data. All attachments go through safe_attach.sh and are detached on failure.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

SAFE = Path(__file__).with_name('safe_attach.sh')
ASSETS = Path('System/ExclaveKit/System/Library/Frameworks/SILManagerComponent.framework/secureindicatorassets')


def inventory(root):
    records = {}
    for path in sorted(root.rglob('*')):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            records[relative] = {'link': os.readlink(path)}
        elif path.is_file():
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            records[relative] = {'size': path.stat().st_size, 'sha256': digest}
    return records


def merge_tree(source, destination):
    for name in ('cam_mic.plist', 'cam_mic_constraints.plist', 'cam_mic.bin'):
        if not (source / ASSETS / name).is_file():
            raise RuntimeError('missing required ExclaveOS asset: ' + name)
    # Copy payload directories, excluding filesystem bookkeeping at the root.
    roots = [p for p in source.iterdir() if not p.name.startswith('.')]
    expected = {}
    for root in roots:
        if not root.is_dir() or root.is_symlink():
            raise RuntimeError('unexpected ExclaveOS root entry: ' + str(root))
        for key, value in inventory(root).items():
            expected[root.name + '/' + key] = value
    if destination.exists():
        if inventory(destination) != expected:
            raise RuntimeError('existing ExclaveOS tree differs; refusing to overwrite it')
        return expected
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name + '.dvm-staging')
    staging.mkdir()  # Fail closed on an interrupted previous merge.
    for root in roots:
        shutil.copytree(root, staging / root.name, symlinks=True)
    if inventory(staging) != expected:
        raise RuntimeError('ExclaveOS copy failed hash/symlink verification')
    staging.rename(destination)
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--exclave', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    for image in (args.image, args.exclave):
        if not image.is_file():
            parser.error('missing image: ' + str(image))
    if args.image.resolve() == args.exclave.resolve():
        parser.error('source and target must differ')
    opened = subprocess.run(['lsof', '-t', str(args.image.resolve())], capture_output=True, text=True)
    if opened.returncode not in (0, 1) or opened.stdout.strip():
        parser.error('target image is open; stop its users before provisioning')
    attached = []
    try:
        source = Path(subprocess.check_output([str(SAFE), 'attach', str(args.exclave), '--readonly'], text=True).strip())
        attached.append(source)
        target = Path(subprocess.check_output([str(SAFE), 'attach', str(args.image), '--owners', 'off', '--volume-name', 'Preboot'], text=True).strip())
        attached.append(target)
        records = merge_tree(source, target / 'Cryptexes/ExclaveOS')
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({'image': str(args.image.resolve()),
            'exclave_source': str(args.exclave.resolve()),
            'guest_path': '/private/preboot/Cryptexes/ExclaveOS',
            'verified_files_and_links': len(records), 'contents': records}, indent=2))
        subprocess.run(['sync'], check=True)
        print('ExclaveOS verified: %d files/links; report=%s' % (len(records), args.report))
    finally:
        detach_errors = []
        for mount in reversed(attached):
            if subprocess.run([str(SAFE), 'detach', str(mount)]).returncode:
                detach_errors.append(str(mount))
        if detach_errors:
            raise RuntimeError('could not detach owned mounts: ' + ', '.join(detach_errors))


if __name__ == '__main__':
    main()
