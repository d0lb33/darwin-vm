#!/usr/bin/env python3
"""Reconstruct ordinary logical charges in a captured compositor allocation stream.

Views add no charge; linear textures retain the backend's separate logical
charge. Native sizes exclude aliases. This is not a native allocation replay
or evidence that retained resources are necessary or eventually released.
"""
import argparse
import json
from pathlib import Path

BPP = {1: 1, 10: 1, 23: 2, 25: 2, 30: 2, 55: 4, 70: 4,
       80: 4, 554: 4, 105: 8, 115: 8}


def charge(op, request):
    if op == 'buffer':
        return request['length']
    if op in ('texture', 'linearTexture'):
        return sum(max(1, request['width'] >> level) *
                   max(1, request['height'] >> level) * request.get('depth', 1) *
                   BPP[request['format']] for level in range(request.get('levels', 1)))
    return 0


def analyze(rows):
    live, failures = {}, []
    peak = released = 0
    for row in rows:
        op, request, reply = row['op'], row['request'], row['reply']
        amount = charge(op, request)
        if not reply.get('ok'):
            if amount:
                total = sum(v['logical_bytes'] for v in live.values())
                failures.append(dict(seq=row['seq'], request=request, reply=reply,
                                     live_logical_bytes=total, requested_logical_bytes=amount,
                                     required_logical_bytes=total + amount,
                                     live_resources=list(live.values())))
            continue
        if op in ('sharedCreate', 'residentCreate'):
            raise ValueError('workload includes allocation types outside this ordinary compositor audit')
        if amount:
            handle = reply['handle']
            if handle in live:
                raise ValueError('live allocation handle reused')
            live[handle] = dict(handle=handle, seq=row['seq'], op=op, request=request,
                                logical_bytes=amount,
                                native_bytes=reply.get('allocatedSize', 0) if op != 'linearTexture' else 0)
        elif op == 'release':
            entry = live.pop(request['handle'], None)
            if entry:
                released += entry['logical_bytes']
        peak = max(peak, sum(v['logical_bytes'] for v in live.values()))
    return dict(scope=__doc__, peak_ordinary_logical_bytes=peak,
                released_ordinary_logical_bytes=released,
                final_ordinary_logical_bytes=sum(v['logical_bytes'] for v in live.values()),
                failed_allocations=failures)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in (args.run / 'driver-host.jsonl').read_text().splitlines()]
    result = analyze(rows)
    (args.run / 'allocation-budget.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'failed_allocations'}, indent=2))
