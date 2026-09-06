#!/usr/bin/env python3
"""Correlate the CPU prerequisite's three scanouts, waits and final pixel oracle.

Run verify_present_pattern.py on last-presented.bgra with --frame 3 first.
"""
import argparse
import json
from pathlib import Path
import re

p=argparse.ArgumentParser(description=__doc__);p.add_argument('trial',type=Path);a=p.parse_args()
r=json.loads((a.trial/'result.json').read_text())
log=(a.trial/'stderr.log').read_text()
events=[e for e in r['events'] if e['line'].startswith('GPU_LOAD_PRESENT_')]
markers=list(re.finditer(r'iomfb: gpu-present frame=(\d+) swap=(\d+) dva=(0x[0-9a-f]+) monotonic_ns=(\d+)',log))
if [int(m[1]) for m in markers]!=[1,2,3]:raise ValueError('three ordered scanouts required')
for i,m in enumerate(markers):
    segment=log[m.end():markers[i+1].start() if i+1<len(markers) else len(log)]
    if f'swap id {m[2]} D594 nested completed, status 0x0' not in segment:raise ValueError('native completion missing')
for i in range(1,4):
    if sum(e['line']==f'GPU_LOAD_PRESENT_RETURN frame={i} op=wait rc=0x0 mode=1' for e in events)!=1:raise ValueError('native mode-1 wait missing')
pixels=json.loads((a.trial/'pixel-verification.json').read_text())
if pixels['frame']!=3 or pixels['bad_pixels']!=0 or pixels['pixels']!=3013524:raise ValueError('final CPU pattern not verified')
report=dict(passed=True,scope='three CPU-pattern owned IOSurface presentations with native D594 completion and mode-1 retirement',
    events=events,scanouts=[dict(frame=int(m[1]),swap=int(m[2]),dva=m[3],host_ns=int(m[4])) for m in markers],pixel_verification=pixels)
(a.trial/'presentation-contract.json').write_text(json.dumps(report,indent=2)+'\n')
print('Three ordered scanouts, successful native completions and waits; zero wrong final pixels.')
