#!/usr/bin/env python3
"""Summarize bounded IOMFB replay evidence; outstanding work is not success."""
import argparse
import json
import re
from pathlib import Path


def report(log, serial):
    pending, counts, errors = {}, {}, []
    def count(key):
        counts[key] = counts.get(key, 0) + 1
    for n, line in enumerate(log.splitlines(), 1):
        if 'iomfb: presented ' in line:
            count('presentations')
        if re.search(r"RPC #\d+ 'A500'", line):
            count('power_rpcs')
        if "completion for 'A500', status 0" in line:
            count('power_rpc_replies')
        start = re.search(r'swap id (\d+) D594 nested on tag (\d+)', line)
        done = re.search(r'swap id (\d+) D594 nested completed, status (0x[0-9a-f]+); releasing A408 tag (\d+)', line)
        if start:
            swap, tag = map(int, start.groups())
            if tag in pending:
                errors.append(f'line {n}: reused occupied tag {tag}')
            pending[tag] = swap
            count('nested_sent')
        if done:
            swap, status, tag = (int(v, 0) for v in done.groups())
            if pending.pop(tag, None) != swap:
                # First completion may belong to the restored migration state.
                count('completion_from_checkpoint_or_missing_start')
            if status:
                errors.append(f'line {n}: callback status {status:#x}')
            count('nested_completed')
        if any(s in line for s in ('unexpected class 2', 'recursive A408',
                                    'D594 send failure', 'send failed')):
            errors.append(f'line {n}: {line}')
    counts['power_off_returns'] = serial.count('set_device_power on=0 kernelAssertCount=0 ret=0')
    counts['power_on_returns'] = serial.count('set_device_power on=1 kernelAssertCount=1 ret=0')
    counts['panics'] = len(re.findall(r'panic\(cpu', serial))
    return dict(counts=counts, pending_at_end=pending, errors=errors)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--log', type=Path, required=True)
    p.add_argument('--serial', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    result = report(a.log.read_text(errors='replace'), a.serial.read_text(errors='replace'))
    a.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
