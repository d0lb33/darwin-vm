#!/usr/bin/env python3
"""Read a paused migration's process list and selected stacks through HMP.

Requires a known live proc pointer from the same checkpoint lineage and its
expected PID/name. This is a diagnosis tool, never a timing observer. Uses
24A5430a layouts from warm_boot_postmortem; no guest writes or LLDB.
"""
import argparse
import json
from pathlib import Path
import re
from warm_boot_postmortem import Memory, inspect, u64

class DemandMemory(Memory):
    def __init__(self, monitor, directory):
        directory.mkdir(parents=True, exist_ok=False)
        super().__init__(monitor, directory)
        self.directory = directory
        self.physical_pages = {}

    def physical(self, address, size):
        output = bytearray()
        while len(output) < size:
            at = address + len(output)
            base = at & ~0x3fff
            if base not in self.physical_pages:
                path = self.directory / f'{base:016x}.bin'
                answer = self.monitor.command(f'pmemsave {base:#x} 0x4000 "{path}"')
                if not path.exists() or path.stat().st_size != 0x4000:
                    raise ValueError(f'physical read failed: {answer}')
                self.physical_pages[base] = path.read_bytes()
            count = min(size-len(output), 0x4000-(at-base))
            output += self.physical_pages[base][at-base:at-base+count]
        return bytes(output)

def identity(raw):
    name = raw[0x55c:0x55c+17].split(b'\0', 1)[0]
    pid = int.from_bytes(raw[0x60:0x64], 'little')
    if not name or any(c < 32 or c > 126 for c in name) or pid > 999999:
        raise ValueError('not a plausible proc')
    if u64(raw, 8) >> 48 != 0xffff or u64(raw, 0x790) >> 48 != 0xffff:
        raise ValueError('invalid proc links/map')
    return name.decode(), pid

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--monitor', required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--seed-proc', type=lambda x: int(x, 0), required=True)
    ap.add_argument('--seed-name', required=True)
    ap.add_argument('--seed-pid', type=int, required=True)
    ap.add_argument('--process', action='append', default=[])
    a = ap.parse_args()
    memory = DemandMemory(Path(a.monitor), a.out / 'process-pages')
    seed = memory.kernel(a.seed_proc, 0x800)
    if identity(seed) != (a.seed_name, a.seed_pid):
        raise RuntimeError('seed is stale or from a different VM')
    todo, seen, inventory, processes = [a.seed_proc], set(), [], []
    while todo:
        address = todo.pop()
        if not address or address in seen:
            continue
        seen.add(address)
        if len(seen) > 4096:
            raise RuntimeError('process-list traversal exceeded bound')
        try:
            raw = memory.kernel(address, 0x800)
            name, pid = identity(raw)
            previous = u64(raw, 8)
            if u64(memory.kernel(previous, 8), 0) != address:
                raise ValueError('proc previous-link mismatch')
        except ValueError:
            continue  # includes the allproc head variable, not itself a proc
        inventory.append(dict(name=name, pid=pid, proc=hex(address)))
        if name in a.process:
            pa = memory.pages[address & ~0x3fff] + (address & 0x3fff)
            processes.append(inspect(memory, name, pa, raw))
        todo.extend([u64(raw, 0), previous])
    (a.out / 'process-inventory.json').write_text(json.dumps(inventory, indent=2)+'\n')
    (a.out / 'migration-processes.json').write_text(json.dumps(processes, indent=2)+'\n')
    print(json.dumps({'processes': len(inventory), 'inspected': [p['name'] for p in processes]}))

if __name__ == '__main__':
    main()
