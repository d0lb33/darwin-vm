#!/usr/bin/env python3
"""Re-pin the installed native-SMC package's boot inputs in place, with backups.

package_native_smc.py builds a whole new package directory, including an 18 GB
disk conversion. When only the launch inputs change (QEMU binary, kernelcache,
device tree, environment) and the disk is untouched, this re-pins those inputs
in the existing package and rewrites default.json's hashes:

    tools/rootfs/repin_native_smc.py --note "PMGR default" \
        --enable ans --enable smc --enable sep --enable dcp --enable spmi \
        --enable pmgr --development-activation --drop-env DARWIN_SMP_PV

Every replaced file is kept beside the new one with a `.pre-<tag>-<sha8>`
suffix, and the previous manifest likewise, so `--restore-backup TAG` can put
it all back. The script refuses to run when the package's current inputs do
not match its manifest (someone edited the package by hand) or when a guest
is running from the package binary.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(8 << 20), b''):
            h.update(c)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--package', type=Path, default=Path.home() / 'dvm-artifacts/native-smc')
    p.add_argument('--qemu', type=Path, default=REPO / 'qemu-sptm/build/qemu-system-aarch64')
    p.add_argument('--bootkc', type=Path, default=REPO / 'firmware/bootkc',
                   help='kernelcache to pin (default: the stock firmware/bootkc)')
    p.add_argument('--dtree-raw', type=Path, default=REPO / 'firmware/dtree.raw')
    p.add_argument('--enable', action='append', default=[],
                   help='dt_fixup -enable features (default: ans smc sep dcp spmi pmgr)')
    p.add_argument('--development-activation', action='store_true')
    p.add_argument('--dram', default='12G')
    p.add_argument('--drop-env', action='append', default=[], help='qemu_env keys to remove')
    p.add_argument('--set-env', action='append', default=[], metavar='KEY=VALUE')
    p.add_argument('--tag', default='pmgr', help='backup suffix tag')
    p.add_argument('--note', required=True, help='qemu_note text recorded in the manifest')
    p.add_argument('--restore-backup', metavar='TAG', help='put the .pre-TAG-* files back and exit')
    a = p.parse_args()

    pkg = a.package.resolve()
    man = pkg / 'default.json'
    m = json.loads(man.read_text())

    if a.restore_backup:
        restore(pkg, man, a.restore_backup)
        return

    binp = pkg / 'qemu-system-aarch64'
    running = subprocess.run(['pgrep', '-f', str(binp)], capture_output=True, text=True).stdout.strip()
    if running:
        sys.exit(f'refusing: a guest is running from {binp} (pids {running})')
    for name, expected in m['qemu_inputs'].items():
        if sha256(Path(name)) != expected['sha256']:
            sys.exit(f'refusing: {name} does not match the manifest; restore or re-pin it first')

    enables = a.enable or ['ans', 'smc', 'sep', 'dcp', 'spmi', 'pmgr']
    argv = m['qemu_argv']

    def arg(key):
        return Path(argv[argv.index(key) + 1])

    tree = arg('-dtree')
    bootkc = arg('-bootkc')
    old_sha = m['qemu_inputs'][str(binp)]['sha256'][:8]
    suffix = f'.pre-{a.tag}-{old_sha}'

    # New device tree first, into a scratch file, so a dt_fixup failure changes nothing.
    new_tree = tree.with_suffix('.dtree.new')
    cmd = [sys.executable, str(REPO / 'dt_fixup.py'), str(a.dtree_raw), str(new_tree),
           '-nvram', str(REPO / 'nvram.bin'), '-dram', a.dram]
    for e in enables:
        cmd += ['-enable', e]
    if a.development_activation:
        cmd.append('-development-activation')
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)

    for f in (binp, bootkc, tree, man):
        shutil.copy2(f, str(f) + suffix)
    shutil.copy2(a.qemu, binp)
    shutil.copy2(a.bootkc, bootkc)
    os.replace(new_tree, tree)

    env = m['qemu_env']
    for k in a.drop_env:
        env.pop(k, None)
    for kv in a.set_env:
        k, v = kv.split('=', 1)
        env[k] = v
    for f in (binp, bootkc, tree):
        m['qemu_inputs'][str(f)] = dict(sha256=sha256(f), bytes=f.stat().st_size)
    m['qemu_note'] = a.note
    m['dtree_recipe'] = dict(enable=enables, development_activation=a.development_activation,
                             dram=a.dram, raw=str(a.dtree_raw))
    tmp = man.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(m, indent=2, sort_keys=True) + '\n')
    os.replace(tmp, man)
    print(f'repinned {pkg}; backups carry {suffix}')
    for f in (binp, bootkc, tree):
        print(f'  {f.name}: {m["qemu_inputs"][str(f)]["sha256"][:16]}')


def restore(pkg, man, tag):
    backups = sorted(pkg.glob(f'*.pre-{tag}-*'))
    if not backups:
        sys.exit(f'no .pre-{tag}-* backups in {pkg}')
    for b in backups:
        original = pkg / b.name.split('.pre-')[0]
        shutil.copy2(b, original)
        print(f'restored {original.name} from {b.name}')


if __name__ == '__main__':
    main()
