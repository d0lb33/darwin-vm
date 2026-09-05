#!/usr/bin/env python3
"""Verify executable remaps and self-modifying code through the real TCG MMU."""
import argparse
import hashlib
from pathlib import Path
from arm_island_bench import ROOT, assemble, run

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--out', type=Path, required=True)
ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
a = ap.parse_args()
a.out.mkdir(exist_ok=False)
code = assemble(a.out, Path(__file__).with_suffix('.S'))
payload = a.out / 'check.bin'
payload.write_bytes(code)
r = run(a.qemu.resolve(), payload, 0, 1000, 'tcg', a.out / 'check')
assert int(r['checksum'], 16) == 31000, r
print('PASS: 1000 executable VA remap cycles and 1000 instruction rewrites; checksum=31000')
print('qemu_sha256=' + hashlib.sha256(a.qemu.read_bytes()).hexdigest())
