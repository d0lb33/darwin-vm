#!/usr/bin/env python3
"""Inspect source-timestamped Home thumbnails; visual review selects animation bounds."""
import argparse
import json
from pathlib import Path
import re
import numpy as np
from PIL import Image, ImageDraw
from report_compositor_pacing import analyze, stats


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('run', type=Path)
    p.add_argument('--first', type=int, help='first visually verified transition thumbnail number')
    p.add_argument('--last', type=int, help='last visually verified transition thumbnail number')
    a = p.parse_args()
    if (a.first is None) != (a.last is None) or (a.first is not None and a.first > a.last):
        p.error('supply an ordered first/last pair')
    text = (a.run/'stderr.log').read_text(errors='replace')
    display = analyze(text)
    completions = {f['present_ns']: f['complete_ns'] for f in display['frames']}
    inputs = [dict(epoch=int(m[1]), seq=int(m[2]), usage=int(m[3]), down=int(m[4]), ns=int(m[5]))
              for m in re.finditer(r'darwin-input: timing epoch=(\d+) seq=(\d+) kind=B a=(\d+) b=(\d+) c=0 monotonic_ns=(\d+)', text)]
    if len(inputs) != 2 or [i['down'] for i in inputs] != [1, 0] or any(i['usage'] != 64 for i in inputs) or inputs[0]['epoch'] != inputs[1]['epoch']:
        raise ValueError('expected one paired Home press in one input epoch')
    baseline = np.asarray(Image.open(a.run/'home-before.ppm').convert('RGB'))[::6, ::6]
    previous = baseline
    frames = []
    for m in re.finditer(r'iomfb: transition file=(transition-(\d+).ppm) present_ns=(\d+) capture_us=(\d+) ok=(\d+)', text):
        name, number, timestamp, overhead, ok = m.groups()
        timestamp = int(timestamp)
        if ok != '1' or timestamp not in completions:
            raise ValueError('capture failed or presentation lacks paired completion')
        pixels = np.asarray(Image.open(a.run/name).convert('RGB'))
        if pixels.shape != baseline.shape:
            raise ValueError('capture geometry changed')
        frames.append(dict(file=name, number=int(number), present_ns=timestamp,
                           input_to_present_ms=(timestamp-inputs[0]['ns'])/1e6,
                           completion_ms=(completions[timestamp]-timestamp)/1e6,
                           capture_us=int(overhead),
                           changed_from_before=float(np.any(pixels != baseline, axis=2).mean()),
                           changed_from_previous=float(np.any(pixels != previous, axis=2).mean())))
        previous = pixels
    if not frames:
        raise ValueError('no transition frames')
    # Review aid only: actual sampled console pixels, no brightness adjustment.
    picked = frames[::max(1, len(frames)//48)]
    sheet = Image.new('RGB', (6*197, ((len(picked)+5)//6)*450), '#333333')
    draw = ImageDraw.Draw(sheet)
    for i, frame in enumerate(picked):
        x, y = (i%6)*197, (i//6)*450
        sheet.paste(Image.open(a.run/frame['file']), (x, y+20))
        draw.text((x+2,y+2), f"{frame['number']} / {frame['input_to_present_ms']:.0f} ms", fill='white')
    sheet.save(a.run/'home-transition-sheet.png')
    report = dict(scope=__doc__, input=inputs, frames=frames,
                  capture_overhead_us=stats([f['capture_us'] for f in frames]),
                  input_to_selected_transition_start_ms=None, transition_gaps_ms=None,
                  limitations='Pixel change is a candidate, not a causal animation oracle. Explicit visual bounds exclude startup/idle. Timings begin at QEMU UART record preparation, not physical button action. Thumbnail sampling can miss fine detail. Capture overhead perturbs this diagnostic run.')
    if a.first is not None:
        chosen = [f for f in frames if a.first <= f['number'] <= a.last]
        if not chosen or chosen[0]['number'] != a.first or chosen[-1]['number'] != a.last:
            raise ValueError('selected visual endpoints not captured')
        if chosen[0]['input_to_present_ms'] < 0:
            raise ValueError('selected visible change precedes input')
        report['visually_selected_bounds'] = [a.first, a.last]
        report['input_to_selected_transition_start_ms'] = chosen[0]['input_to_present_ms']
        report['transition_gaps_ms'] = stats([(b['present_ns']-f['present_ns'])/1e6 for f,b in zip(chosen,chosen[1:])])
    (a.run/'home-transition.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'frames'}, indent=2))


if __name__ == '__main__':
    main()
