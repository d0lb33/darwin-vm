#!/usr/bin/env python3
"""Frame timeline of one traced gesture (capture_home_trial.py --input ...).

report_home_transition.py requires exactly one Home button pair; this reads
any mix of QEMU ``darwin-input: timing`` records (touch D/M/U or button B) and
pairs the source-timestamped transition thumbnails with them:

* first input record and last input record (gesture duration on QEMU's clock);
* every presentation after the first input: delay since the first input,
  fraction of sampled pixels changed from the previous thumbnail;
* the animation phase: frames whose change exceeds ``--changed`` (default
  0.2% of sampled pixels), its first/last delay and the intervals inside it.

Thumbnail change is a candidate for "something moved", not a semantic oracle.
Timings start at QEMU's UART record preparation, not a physical touch.
"""
import argparse
import json
from pathlib import Path
import re

import numpy as np
from PIL import Image

from report_compositor_pacing import analyze, stats


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('run', type=Path, help='capture_home_trial.py output directory')
    p.add_argument('--changed', type=float, default=0.002, help='changed-pixel fraction that counts as animation')
    a = p.parse_args()
    text = (a.run / 'stderr.log').read_text(errors='replace')
    display = analyze(text)
    completions = {f['present_ns']: f['complete_ns'] for f in display['frames']}
    inputs = [dict(epoch=int(m[1]), seq=int(m[2]), kind=m[3], a=int(m[4]), b=int(m[5]), ns=int(m[6]))
              for m in re.finditer(r'darwin-input: timing epoch=(\d+) seq=(\d+) kind=([A-Z]) a=(\d+) b=(\d+) c=\d+ monotonic_ns=(\d+)', text)]
    if not inputs:
        raise ValueError('no input timing records; run with DARWIN_INPUT_TIMING=1')
    first_ns, last_ns = inputs[0]['ns'], inputs[-1]['ns']
    before = a.run / 'home-before.ppm'
    baseline = np.asarray(Image.open(before).convert('RGB'))[::6, ::6] if before.exists() else None
    previous = baseline
    frames = []
    for m in re.finditer(r'iomfb: transition file=(transition-(\d+).ppm) present_ns=(\d+) capture_us=(\d+) ok=(\d+)', text):
        name, number, timestamp, overhead, ok = m.groups()
        timestamp = int(timestamp)
        if ok != '1' or timestamp not in completions:
            raise ValueError('capture failed or presentation lacks paired completion')
        pixels = np.asarray(Image.open(a.run / name).convert('RGB'))
        changed = float(np.any(pixels != previous, axis=2).mean()) if previous is not None and previous.shape == pixels.shape else None
        frames.append(dict(file=name, number=int(number), present_ns=timestamp,
                           since_first_input_ms=(timestamp - first_ns) / 1e6,
                           completion_ms=(completions[timestamp] - timestamp) / 1e6,
                           capture_us=int(overhead), changed_from_previous=changed))
        previous = pixels
    after = [f for f in frames if f['present_ns'] >= first_ns]
    moving = [f for f in after if f['changed_from_previous'] is not None and f['changed_from_previous'] >= a.changed]
    intervals = [(b['present_ns'] - f['present_ns']) / 1e6 for f, b in zip(after, after[1:])]
    summary = dict(
        scope=__doc__.strip().splitlines()[0],
        inputs=len(inputs), input_kinds=''.join(i['kind'] for i in inputs),
        gesture_ms=(last_ns - first_ns) / 1e6,
        frames_after_input=len(after),
        first_frame_ms=after[0]['since_first_input_ms'] if after else None,
        first_moving_frame_ms=moving[0]['since_first_input_ms'] if moving else None,
        last_moving_frame_ms=moving[-1]['since_first_input_ms'] if moving else None,
        moving_frames=len(moving),
        moving_phase_intervals_ms=stats([(b['present_ns'] - f['present_ns']) / 1e6
                                         for f, b in zip(moving, moving[1:])]) if len(moving) > 1 else None,
        all_intervals_after_input_ms=stats(intervals) if intervals else None,
        largest_gaps_ms=sorted([(round(x, 1), round(after[i]['since_first_input_ms'], 1)) for i, x in enumerate(intervals)],
                               reverse=True)[:5],
        capture_overhead_us=stats([f['capture_us'] for f in frames]) if frames else None)
    (a.run / 'gesture-timeline.json').write_text(json.dumps(dict(summary=summary, inputs=inputs, frames=frames), indent=2) + '\n')
    print(json.dumps(summary, indent=2))
    print('frames (ms since first input, changed fraction):')
    print(' '.join('%d/%.3f' % (f['since_first_input_ms'], f['changed_from_previous'] or 0) for f in after))


if __name__ == '__main__':
    main()
