#!/usr/bin/env python3
"""Point a validated boot manifest at the disk state that precedes its install.

`run_guest_install.py` installs into a fresh child of the manifest's disk, and
the guarded installer refuses a System volume that already carries a bootstrap.
This keeps every pinned QEMU/kernel/SPTM/TXM/device-tree input of the validated
package and swaps in the parent that package was itself installed onto.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import atomic_json, qcow2_backing_chain, sha256, verify_backing_chain


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest', type=Path, help='validated package manifest supplying every pinned input')
    p.add_argument('disk', type=Path, help='pre-install parent disk, normally the manifest chain\'s next entry')
    p.add_argument('out', type=Path)
    p.add_argument('--bootkc', type=Path,
                   help='replace the pinned BootKC, for an opt-in kernel addition built from the same source image')
    a = p.parse_args()
    source = json.loads(a.manifest.read_text())
    chain = source['disk']['backing_chain']
    verify_backing_chain(chain)
    disk = a.disk.resolve()
    if str(disk) not in [entry['path'] for entry in chain]:
        p.error('requested parent is not in the validated package backing chain')
    for name, item in source['qemu_inputs'].items():
        if sha256(Path(name)) != item['sha256']:
            raise SystemExit('changed pinned input '+name)
    derived = {key: source[key] for key in ('format', 'qemu_argv', 'qemu_inputs', 'qemu_env')}
    derived['source_manifest'] = str(a.manifest.resolve())
    derived['scope'] = 'validated package inputs with its own pre-install parent disk'
    derived['disk'] = dict(path=str(disk),
                           backing_chain=qcow2_backing_chain(Path(shutil.which('qemu-img')), disk))
    argv = derived['qemu_argv']
    argv[argv.index('-drive')+1] = f'if=none,id=ans,file={disk},format=qcow2'
    if a.bootkc:
        bootkc = a.bootkc.resolve()
        derived['qemu_inputs'].pop(argv[argv.index('-bootkc')+1], None)
        argv[argv.index('-bootkc')+1] = str(bootkc)
        derived['qemu_inputs'][str(bootkc)] = dict(bytes=bootkc.stat().st_size, sha256=sha256(bootkc))
        derived['bootkc_replaced'] = True
    verify_backing_chain(derived['disk']['backing_chain'])
    atomic_json(a.out, derived)
    print(json.dumps(dict(out=str(a.out), disk=str(disk), chain=len(derived['disk']['backing_chain'])), indent=2))


if __name__ == '__main__':
    main()
