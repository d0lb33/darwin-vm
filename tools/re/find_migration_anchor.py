#!/usr/bin/env python3
"""Find a live 24A5430a proc anchor in a bounded paused physical RAM scan.

Uses the allproc previous-link backlink and revalidates the virtual proc;
never assumes addresses or PIDs from a different boot. Read-only diagnosis.
"""
import argparse
import json
from pathlib import Path
from inspect_migration_processes import DemandMemory, identity
from warm_boot_postmortem import Memory, u64


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--monitor', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--name', default='cfprefsd')
    ap.add_argument('--base', type=lambda s: int(s, 0), required=True)
    ap.add_argument('--size', type=lambda s: int(s, 0), default=0x80000000)
    a = ap.parse_args()
    memory = DemandMemory(a.monitor, a.out / 'pages')
    chunks = a.out / 'chunks'
    chunks.mkdir()
    for offset in range(0, a.size, 0x10000000):
        base = a.base + offset
        size = min(0x10000000, a.size-offset)
        path = chunks / f'{base:016x}.bin'
        answer = memory.monitor.command(f'pmemsave {base:#x} {size:#x} "{path}"')
        if not path.exists() or path.stat().st_size != size:
            raise RuntimeError(f'RAM capture failed: {answer}')
        scan = Memory(a.monitor, chunks)
        try:
            for pa, raw in scan.candidates(a.name):
                try:
                    name, pid = identity(raw)
                    address = u64(memory.kernel(u64(raw, 8), 8), 0)
                    actual = memory.kernel(address, 0x800)
                    if actual != raw:
                        continue
                    result = dict(name=name, pid=pid, proc=hex(address),
                                  proc_pa=hex(pa), scanned_bytes=offset+size)
                    (a.out/'anchor.json').write_text(json.dumps(result, indent=2)+'\n')
                    print(json.dumps(result), flush=True)
                    return
                except ValueError:
                    continue
        finally:
            for _, mapped in scan.maps:
                mapped.close()
        print(f'No validated anchor in first {offset+size:#x} bytes', flush=True)
        path.unlink()
    raise RuntimeError('No anchor in bounded scan; do not infer process absence')


if __name__ == '__main__':
    main()
