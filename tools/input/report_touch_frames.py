#!/usr/bin/env python3
"""Compare captured scanout thumbnails with a pre-touch console image.

Pixel differences are review candidates, not proof of causal UI response.
"""
import argparse
import json
from pathlib import Path
import re
import numpy as np
from PIL import Image


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--frames',type=Path,required=True)
    p.add_argument('--before',type=Path,required=True)
    p.add_argument('--seq',type=int,required=True)
    p.add_argument('--epoch',type=int,required=True)
    p.add_argument('--seconds',type=float,default=5)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    stderr=a.run/('stderr.log' if (a.run/'stderr.log').exists()
                  else 'qemu.stderr.log')
    text=stderr.read_text(errors='replace')
    pattern=rf'darwin-input: timing epoch={a.epoch} seq={a.seq} kind=D a=\d+ b=\d+ c=0 monotonic_ns=(\d+)'
    stamps=re.findall(pattern,text)
    if len(stamps)!=1:raise ValueError('expected one touch-down origin')
    start=int(stamps[0]);base=np.asarray(Image.open(a.before).convert('RGB'))[::6,::6]
    rows=[]
    for name,stamp,cost,ok in re.findall(r'iomfb: transition file=(\S+) present_ns=(\d+) capture_us=(\d+) ok=(\d+)',text):
        delta=(int(stamp)-start)/1e6
        if not 0<=delta<=a.seconds*1000:continue
        if ok!='1':raise ValueError('capture failed')
        frame=np.asarray(Image.open(a.frames/name).convert('RGB'))
        if frame.shape!=base.shape:raise ValueError('geometry mismatch')
        # Exclude status-bar clock and bottom affordances; retain UI content.
        y0,y1=frame.shape[0]//10,frame.shape[0]*9//10
        diff=np.max(np.abs(frame[y0:y1].astype(np.int16)-base[y0:y1].astype(np.int16)),axis=2)
        rows.append(dict(file=name,wire_to_present_ms=delta,capture_us=int(cost),content_changed_fraction=float((diff>8).mean())))
    if not rows:raise ValueError('no captured frames in selected interval')
    a.out.write_text(json.dumps(dict(epoch=a.epoch,seq=a.seq,frames=rows,limitations='Wire-preparation origin excludes prior host queueing. Scanout thumbnails require visual verification; unrelated changes can pass pixel thresholds. Capture overhead is recorded; this is not client display latency.'),indent=2)+'\n')
    print(json.dumps(rows[:12],indent=2))


if __name__=='__main__':main()
