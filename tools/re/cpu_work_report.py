#!/usr/bin/env python3
"""Summarize bounded DVM_WORK diagnostic samples, not total VM wall time."""
import argparse
import json
from pathlib import Path
import re


def summarize(text):
    latest = {}
    samples = 0
    for line in text.splitlines():
        if not line.startswith('DVM_WORK '):
            continue
        row = {k: int(v) for k, v in re.findall(r'(\w+)=(\d+)', line)}
        required = {'cpu', 'count', 'adjacent', 'max_depth', 'wait_ns',
                    'body_end_ns', 'relock_ns', 'max_wait_ns'}
        if not required <= row.keys():
            raise ValueError('incomplete diagnostic line: ' + line)
        previous = latest.get(row['cpu'])
        if previous and row['count'] <= previous['count']:
            raise ValueError('counter reset or mixed runs')
        latest[row['cpu']] = row
        samples += 1
    count = sum(r['count'] for r in latest.values())
    adjacent = sum(r['adjacent'] for r in latest.values())
    return {
        'samples': samples,
        'cpus': sorted(latest.values(), key=lambda r: r['cpu']),
        'reported_exclusive_items': count,
        'immediate_exclusive_successors': adjacent,
        'immediate_exclusive_successor_fraction': adjacent / count if count else None,
        'summed_wait_seconds': sum(r['wait_ns'] for r in latest.values()) / 1e9,
        'summed_body_and_end_seconds': sum(r['body_end_ns'] for r in latest.values()) / 1e9,
        'summed_bql_relock_seconds': sum(r['relock_ns'] for r in latest.values()) / 1e9,
        'limits': 'Last periodic sample per CPU; excludes unreported tail. Waits overlap across CPUs. Depth is remaining queue at dequeue, capped at 64. Instrumented timing is not a performance comparison.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.log.read_text(errors='replace')), indent=2))


if __name__ == '__main__':
    main()
