#!/usr/bin/env python3
"""Final-only comparison of the CPU presentation prerequisite's entire PNG."""
import argparse,hashlib,json
from pathlib import Path
from PIL import Image
p=argparse.ArgumentParser();p.add_argument('screen',type=Path);p.add_argument('out',type=Path);p.add_argument('--frame',type=int,choices=(1,2,3),default=1);a=p.parse_args()
if a.screen.suffix=='.bgra':
    raw=a.screen.read_bytes()
    if len(raw)!=4864*2556:raise ValueError('unexpected scanout byte count')
    im=Image.frombytes('RGB',(1179,2556),raw,'raw','BGRX',4864,1)
else:im=Image.open(a.screen).convert('RGB')
if im.size!=(1179,2556):raise ValueError('unexpected display geometry')
w,h=im.size;expected=bytearray(w*h*3);marker=[0xff44564d,0xff505253,0xff424c52,0xff000000|a.frame]
for y in range(h):
    for x in range(w):
        v=marker[x] if not y and x<4 else 0xff000000|((x*255//w)<<16)|((y*255//h)<<8)|(a.frame*70)
        i=(y*w+x)*3;expected[i:i+3]=bytes(((v>>16)&255,(v>>8)&255,v&255))
actual=im.tobytes();bad=sum(actual[i:i+3]!=expected[i:i+3] for i in range(0,len(actual),3))
r=dict(screen=str(a.screen.resolve()),frame=a.frame,width=w,height=h,bad_pixels=bad,pixels=w*h,actual_rgb_sha256=hashlib.sha256(actual).hexdigest(),expected_rgb_sha256=hashlib.sha256(expected).hexdigest(),scope='final normal-display screenshot; CPU prerequisite, not GPU workload')
a.out.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r));raise SystemExit(bool(bad))
