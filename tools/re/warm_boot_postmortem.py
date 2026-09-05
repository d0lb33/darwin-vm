#!/usr/bin/env python3
"""Inspect a frozen 24A5430a warm boot using HMP and its physical RAM dump.

No debugger connection or guest writes. Layout evidence is shared with
physical_task_memory.py and live_task_threads.py. The monitor must belong to
the same, still-paused VM that supplied the complete dump.
"""
import argparse
import json
import mmap
from pathlib import Path
import re
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import HMP


class Memory:
    def __init__(self, monitor, directory):
        self.monitor = HMP(monitor, timeout=10)
        if 'paused' not in self.monitor.command('info status'):
            raise RuntimeError('requires the frozen source VM')
        # gva2gpa honors the selected CPU's privilege. An EL0 CPU cannot
        # translate these kernel mappings, even when the process is alive.
        cpus = re.findall(r'CPU #(\d+):', self.monitor.command('info cpus'))
        for cpu in cpus:
            self.monitor.command(f'cpu {cpu}')
            if re.search(r'EL[12][th]', self.monitor.command('info registers')):
                break
        else:
            raise RuntimeError('no frozen privileged CPU available for kernel translation')
        self.maps = []
        self.pages = {}
        for path in sorted(directory.glob('*.bin')):
            with path.open('rb') as f:
                self.maps.append((int(path.stem, 16), mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)))

    def physical(self, address, size):
        for base, data in self.maps:
            if base <= address and address + size <= base + len(data):
                return data[address-base:address-base+size]
        raise ValueError(f'physical range absent: {address:#x}+{size:#x}')

    def kernel(self, address, size):
        output = bytearray()
        while len(output) < size:
            va = address + len(output)
            page = va & ~0x3fff
            if page not in self.pages:
                answer = self.monitor.command(f'gva2gpa {page:#x}')
                match = re.search(r'gpa: 0x([0-9a-f]+)', answer)
                if not match:
                    raise ValueError(f'kernel translation failed: {va:#x}: {answer}')
                self.pages[page] = int(match[1], 16)
            count = min(size-len(output), 0x4000-(va & 0x3fff))
            output += self.physical(self.pages[page] + (va & 0x3fff), count)
        return bytes(output)

    def user(self, root, address, size):
        if not 0 <= address < 1 << 43:
            raise ValueError(f'user address outside supported geometry: {address:#x}')
        output = bytearray()
        while len(output) < size:
            va = address + len(output)
            table = root & 0x0000fffffffffc00
            for level, shift in ((1, 36), (2, 25), (3, 14)):
                index = (va >> shift) & (0x7f if level == 1 else 0x7ff)
                entry = int.from_bytes(self.physical(table+index*8, 8), 'little')
                if not entry & 1 or (level == 3 and entry & 3 != 3):
                    raise ValueError(f'unmapped user address: {va:#x}')
                if entry & 3 == 1 or level == 3:
                    mask = (1 << shift)-1
                    pa = (entry & 0x0000ffffffffc000 & ~mask) | (va & mask)
                    count = min(size-len(output), (1 << shift)-(va & mask))
                    output += self.physical(pa, count)
                    break
                table = entry & 0x0000ffffffffc000
        return bytes(output)

    def candidates(self, name):
        for base, data in self.maps:
            at = data.find(name.encode()+b'\0')
            while at >= 0:
                offset = at-0x55c
                if offset >= 0 and offset % 8 == 0 and offset+0x800 <= len(data):
                    raw = data[offset:offset+0x800]
                    # The final element of allproc legitimately has no next
                    # pointer. Its pointer-to-previous-link remains in kernel
                    # memory; do not omit that live task from a frozen scan.
                    next_proc = u64(raw, 0)
                    if (next_proc == 0 or next_proc >> 48 == 0xffff) and \
                            u64(raw, 8) >> 48 == 0xffff and \
                            u64(raw, 0x790) >> 48 == 0xffff:
                        yield base+offset, raw
                at = data.find(name.encode()+b'\0', at+1)


def u64(raw, offset):
    return struct.unpack_from('<Q', raw, offset)[0]


def inspect(memory, name, pa, raw):
    vmmap = u64(raw, 0x790)
    pmap = u64(memory.kernel(vmmap+0x58, 8), 0)
    root = u64(memory.kernel(pmap+8, 8), 0)
    address = u64(raw, 0x7b8)
    rows, seen = [], set()
    for _ in range(256):
        if not address or address in seen:
            break
        seen.add(address)
        data = memory.kernel(address, 0x440)
        if u64(data, 0x1a0) != 0x2010002030100000:
            break
        saved = u64(data, 0x110)
        row = dict(thread=hex(address), wait_event=hex(u64(data, 0x18)),
                   continuation=hex(u64(data, 0xe0)), kernel_stack=hex(u64(data, 0xf0)))
        row['cthread_self'] = hex(u64(data, 0x140))
        try:
            state = memory.kernel(saved, 0x110)
            if int.from_bytes(state[:4], 'little') != 0x15:
                raise ValueError('unsupported saved-state flavor')
            row.update(pc=hex(u64(state, 0x108)), lr=hex(u64(state, 0xf8)), frames=[])
            row['sp'] = hex(u64(state, 0x100))
            row['registers'] = {f'x{i}': hex(u64(state, 8 + i * 8)) for i in range(29)}
            fp, frames_seen = u64(state, 0xf0), set()
            for _ in range(48):
                if not fp or fp & 7 or fp in frames_seen:
                    break
                frames_seen.add(fp)
                frame = memory.user(root, fp, 16)
                row['frames'].append(hex(u64(frame, 8) & 0xffffffffffff))
                parent = u64(frame, 0)
                if parent <= fp:
                    break
                fp = parent
        except ValueError as error:
            row['read_error'] = str(error)
        rows.append(row)
        address = u64(data, 0x408)
    return dict(name=name, pid=struct.unpack_from('<I', raw, 0x60)[0],
                proc_pa=hex(pa), map=hex(vmmap), root=hex(root), threads=rows)


def resolve_slide(memory, process, cache_dir):
    root = int(process['root'], 16)
    # A saved frame's instruction sequence gives a slide without changing the
    # currently selected CPU or running the guest to catch an EL0 boundary.
    address = next(int(a, 16) for t in process['threads'] for a in t.get('frames', [])
                   if 0x180000000 <= int(a, 16) < 0x340000000)
    pattern = memory.user(root, address, 64)
    hits = []
    for path in sorted(cache_dir.glob('dyld_shared_cache_arm64e*')):
        if path.suffix in ('.a2s', '.symbols', '.map', '.atlas'):
            continue
        with path.open('rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            offset = data.find(pattern)
            while offset >= 0:
                mapping_offset, count = struct.unpack_from('<II', data, 0x10)
                for index in range(count):
                    va, size, fileoff = struct.unpack_from('<QQQ', data, mapping_offset+index*32)
                    if fileoff <= offset < fileoff+size:
                        static = va+offset-fileoff
                        slide = address-static
                        if 0 <= slide <= 0x20000000 and slide % 0x4000 == 0:
                            hits.append(dict(runtime=hex(address), static=hex(static), slide=hex(slide),
                                             file=str(path), file_offset=hex(offset)))
                offset = data.find(pattern, offset+1)
    if len(hits) != 1:
        raise ValueError(f'expected unique shared-cache slide, got {hits}')
    return hits[0]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('run', type=Path)
    p.add_argument('--process', action='append', default=[])
    p.add_argument('--cache-dir', type=Path)
    args = p.parse_args()
    launch = json.loads((args.run/'launch.json').read_text())
    argv = launch['argv']
    endpoint = argv[argv.index('-monitor') + 1]
    if not endpoint.startswith('unix:'):
        p.error('requires the recorded UNIX monitor endpoint')
    memory = Memory(Path(endpoint[5:].split(',', 1)[0]), args.run/'ram')
    result = []
    for name in args.process or ['backboardd', 'SpringBoard', 'dvm-input', 'mediaserverd']:
        candidates = list(memory.candidates(name))
        for pa, raw in candidates:
            try:
                result.append(inspect(memory, name, pa, raw))
            except ValueError as error:
                # Freed process objects retain names and plausible pointers.
                # Record their failed validation, then examine other candidates.
                result.append(dict(name=name, proc_pa=hex(pa), read_error=str(error)))
        print(f'{name}: {len(candidates)} candidate(s)', flush=True)
    (args.run/'process-stacks.json').write_text(json.dumps(result, indent=2)+'\n')
    readable = [r for r in result if r.get('threads')]
    if args.cache_dir and readable:
        slide = resolve_slide(memory, readable[0], args.cache_dir)
        (args.run/'slide.json').write_text(json.dumps(slide, indent=2)+'\n')
        print(json.dumps(slide))


if __name__ == '__main__':
    main()
