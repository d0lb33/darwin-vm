#!/usr/bin/env python3
"""Actual QEMU presentation timestamps; idle gaps are not inferred frame misses."""
import argparse
import json
import math
from pathlib import Path
import re


def stats(values):
    if not values:
        return None
    ordered = sorted(values)
    return dict(count=len(values), minimum=ordered[0], maximum=ordered[-1],
                mean=sum(values)/len(values),
                **{'p'+str(p): ordered[max(0, math.ceil(len(values)*p/100)-1)] for p in (50, 95, 99)})


def analyze(text):
    frames = []
    pending = None
    for line in text.splitlines():
        if line.startswith('iomfb: presented '):
            match = re.search(r'swap=(\d+) scanout_us=(\d+) monotonic_ns=(\d+)', line)
            if not match:
                raise ValueError('presentation lacks source timestamp')
            if pending is not None:
                raise ValueError('presentation overlaps unfinished native completion')
            swap, copy_us, timestamp = map(int, match.groups())
            if frames and timestamp <= frames[-1]['present_ns']:
                raise ValueError('nonmonotonic presentation timestamp')
            pending = dict(swap=swap, present_ns=timestamp, scanout_us=copy_us)
        match = re.match(r'iomfb: swap id (\d+) D594 (?:nested )?completed, status 0x([0-9a-f]+).*monotonic_ns=(\d+)', line)
        if match:
            swap, status, timestamp = int(match[1]), int(match[2],16), int(match[3])
            if status:
                raise ValueError('native completion failure')
            if pending is None:
                continue  # Earlier non-pixel swaps are outside the measured set.
            if swap != pending['swap'] or timestamp < pending['present_ns']:
                raise ValueError('completion identity/time mismatch')
            pending['complete_ns'] = timestamp
            frames.append(pending)
            pending = None
    if pending is not None:
        raise ValueError('uncompleted final presentation')
    intervals = [(b['present_ns']-a['present_ns'])/1e6 for a,b in zip(frames,frames[1:])]
    span = (frames[-1]['present_ns']-frames[0]['present_ns'])/1e9 if len(frames)>1 else 0
    return dict(scope='source-timestamped QEMU framebuffer delivery and guest D594 acknowledgement; not physical display refresh',
                frames=frames, presentations=len(frames), presentation_span_s=span,
                observed_presentations_per_second=(len(frames)-1)/span if span else None,
                inter_presentation_ms=stats(intervals),
                after_first_eight_inter_presentation_ms=stats(intervals[8:]),
                scanout_ms=stats([f['scanout_us']/1000 for f in frames]),
                presentation_to_completion_ms=stats([(f['complete_ns']-f['present_ns'])/1e6 for f in frames]),
                gap_counts={str(limit)+'ms':sum(x>limit for x in intervals) for limit in (16.667,33.334,50,100,1000)},
                slowest_intervals=[dict(after_frame=i+1,interval_ms=x) for i,x in sorted(enumerate(intervals),key=lambda pair:pair[1],reverse=True)[:10]],
                limitations='No frame-demand/deadline oracle: idle or display-off intervals cannot be labelled missed frames. Startup, input-triggered work and idle are not interchangeable. Host GPU/service clocks are not assumed synchronized to this clock.',
                native_60fps_verified=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    args=parser.parse_args()
    result=analyze((args.run/'stderr.log').read_text(errors='replace'))
    (args.run/'presentation-pacing.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='frames'},indent=2))


if __name__=='__main__':
    main()
