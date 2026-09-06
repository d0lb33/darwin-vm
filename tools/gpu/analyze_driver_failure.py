#!/usr/bin/env python3
"""Analyze an owned, complete failure snapshot offline; never contact a VM."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import mmap
from pathlib import Path
import re
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def analyze(run, cache_dir=None):
    directory = run / 'failure-snapshot'
    report = json.loads((directory / 'result.json').read_text())
    if not report.get('complete') or report['bytes'] != 12 * 1024**3:
        raise ValueError('requires complete 12 GiB capture')
    spec = importlib.util.spec_from_file_location('saved_reader', directory / 'reader.py')
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    memory = reader.Memory.__new__(reader.Memory)
    memory.maps = []
    memory.pages = {int(va, 16): int(pa, 16) for va, pa in report.get('kernel_pages', {}).items()}
    for path in sorted((directory / 'ram').glob('*.bin')):
        with path.open('rb') as stream:
            memory.maps.append((int(path.stem, 16), mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)))
    registers = (run / 'registers.txt').read_text()
    regs = {k: int(v, 16) for k, v in re.findall(r'(PC|SP|X\d\d)=([0-9a-f]+)', registers)}
    output = dict(source_sha256={name: hashlib.sha256((run / name).read_bytes()).hexdigest()
                               for name in ('registers.txt', 'failure-snapshot/result.json', 'stderr.log')},
                  processes=[], live_cpu_registers={k: hex(v) for k, v in regs.items()})
    try:
        for process in report['processes']:
            # Early snapshot collector accidentally reused the name variable
            # for a stack filename. Recover the process name from its raw proc.
            raw = memory.physical(int(process['proc_pa'], 16), 0x800)
            name = raw[0x55c:0x57c].split(b'\0')[0].decode()
            if name != 'dvm-gpu-load':
                continue
            entry = dict(pid=process['pid'], name=name, captured_label=process['name'], threads=[])
            if cache_dir:
                entry['dyld_slide'] = reader.resolve_slide(memory, process, cache_dir)
            for thread in process.get('threads', []):
                row = dict(thread=thread['thread'], saved_user_pc=thread.get('pc'),
                           wait_event=thread['wait_event'])
                address = int(thread['thread'], 16)
                page = address & ~0x3fff
                if page in memory.pages:
                    data = memory.physical(memory.pages[page] + (address & 0x3fff), 0x440)
                    row['scheduler_state_raw'] = hex(struct.unpack_from('<I', data, 0x1f0)[0])
                stack = int(thread['kernel_stack'], 16)
                fp = regs.get('X29', 0)
                if stack and stack <= regs.get('SP', 0) < stack + 0x4000 and stack <= fp < stack + 0x4000:
                    data = (directory / thread['kernel_stack_file']).read_bytes()
                    row['live_cpu_stack_match'] = True
                    row['live_pc'] = hex(regs['PC'])
                    frames = []
                    for _ in range(64):
                        if not stack <= fp <= stack + len(data) - 16 or fp % 16:
                            break
                        parent, lr = struct.unpack_from('<QQ', data, fp-stack)
                        parent |= 0xffff000000000000
                        lr |= 0xffff000000000000
                        if not 0xfffffff007004000 <= lr < 0xfffffff040000000:
                            break
                        frames.append(dict(fp=hex(fp), lr=hex(lr)))
                        if parent <= fp:
                            break
                        fp = parent
                    row['live_frame_chain'] = frames
                entry['threads'].append(row)
            output['processes'].append(entry)
        trace = [line for line in (run / 'stderr.log').read_text(errors='replace').splitlines()
                 if 'AUXTRACE stage=' in line]
        output['ans_trace_lines'] = len(trace)
        output['ans_trace_counts'] = dict(Counter(re.search(r'AUXTRACE stage=(\w+)', line)[1] for line in trace))
        output['ans_trace_tail'] = trace[-32:]
        output['limitations'] = 'One paused CPU sample establishes context, not a persistent spin or deadlock. No timing crosses clock domains.'
        return output
    finally:
        for _, data in memory.maps:
            data.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--cache-dir', type=Path)
    args = parser.parse_args()
    result = analyze(args.run, args.cache_dir)
    path = args.run / 'failure-analysis.json'
    path.write_text(json.dumps(result, indent=2) + '\n')
    print(path)
