#!/usr/bin/env python3
"""Pin a TCG executable for same-disk boot comparisons, without changing defaults."""
import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import sha256, verify_backing_chain


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest', type=Path)
    p.add_argument('qemu', type=Path)
    p.add_argument('out', type=Path)
    p.add_argument('--tb-size-mib', type=int, help='optional host translation-cache capacity')
    a = p.parse_args()
    if a.tb_size_mib is not None and not 1 <= a.tb_size_mib <= 16384:
        p.error('--tb-size-mib must be 1..16384')
    m = json.loads(a.manifest.read_text())
    verify_backing_chain(m['disk']['backing_chain'])
    for path, item in m['qemu_inputs'].items():
        if sha256(Path(path)) != item['sha256']:
            p.error('changed input: ' + path)
    if '-accel' not in m['qemu_argv'] or not m['qemu_argv'][m['qemu_argv'].index('-accel') + 1].startswith('tcg'):
        p.error('requires an existing TCG manifest')
    a.out.mkdir(parents=True, exist_ok=False)
    binary = (a.out / 'qemu-system-aarch64').resolve()
    shutil.copy2(a.qemu, binary)
    shutil.copy2(a.qemu.with_name('qemu-img'), a.out / 'qemu-img')
    previous = m['qemu_argv'][0]
    m['qemu_inputs'].pop(previous, None)
    m['qemu_argv'][0] = str(binary)
    m['qemu_inputs'][str(binary)] = dict(bytes=binary.stat().st_size, sha256=sha256(binary))
    m['tcg_comparison'] = dict(source_manifest=str(a.manifest.resolve()),
                               previous_qemu=previous, source_binary=str(a.qemu.resolve()),
                               changed='QEMU executable only; disk, firmware, CPU topology and environment preserved')
    if a.tb_size_mib is not None:
        index = m['qemu_argv'].index('-accel') + 1
        opts = [v for v in m['qemu_argv'][index].split(',') if not v.startswith('tb-size=')]
        m['qemu_argv'][index] = ','.join(opts + [f'tb-size={a.tb_size_mib}'])
        m['tcg_comparison']['translation_cache_mib'] = a.tb_size_mib
        m['tcg_comparison']['changed'] += '; explicit host translation-cache capacity override'
    (a.out / 'candidate.json').write_text(json.dumps(m, indent=2) + '\n')
    print(a.out / 'candidate.json')


if __name__ == '__main__':
    main()
