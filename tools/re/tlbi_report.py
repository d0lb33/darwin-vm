#!/usr/bin/env python3
"""Summarize periodic exact-guest TLBI counters; totals exclude unsampled tails."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re


def summarize(text):
    if 'DVM_TLBI_OVERFLOW' in text:
        raise ValueError('instrumentation slots exhausted')
    latest = {}
    for line in text.splitlines():
        if not line.startswith('DVM_TLBI '):
            continue
        fields = dict(re.findall(r'(\w+)=(\S+)', line))
        key = (int(fields['cpu']), fields['name'])
        row = dict(cpu=key[0], name=key[1], count=int(fields['count']),
                   pc=fields['pc'], value=fields['value'])
        if key in latest and row['count'] <= latest[key]['count']:
            raise ValueError('counter reset, duplicate names, or mixed runs')
        latest[key] = row
    totals = Counter()
    for row in latest.values():
        totals[row['name']] += row['count']
    return dict(reported_counts=dict(totals.most_common()),
                per_cpu=sorted(latest.values(), key=lambda r: (r['cpu'], r['name'])),
                limits='Periodic per-register samples; excludes tails. PCs and operands are sampled values, not a full instruction trace. Counts do not measure time.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('log', type=Path)
    args = p.parse_args()
    print(json.dumps(summarize(args.log.read_text(errors='replace')), indent=2))


if __name__ == '__main__':
    main()
