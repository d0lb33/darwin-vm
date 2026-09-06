#!/usr/bin/env python3
"""Compare a diagnostic 64x48 host BGRA result with guest-display PNG pixels."""
import argparse, hashlib, json
from pathlib import Path
from PIL import Image
p=argparse.ArgumentParser();p.add_argument('bgra',type=Path);p.add_argument('screen',type=Path);p.add_argument('out',type=Path);a=p.parse_args()
im=Image.open(a.screen).convert('RGB');assert im.size==(1179,2556)
rect=im.crop((100,100,164,148)).tobytes();bgra=a.bgra.read_bytes();assert len(bgra)==64*48*4
expected=bytes(v for i in range(0,len(bgra),4) for v in (bgra[i+2],bgra[i+1],bgra[i]))
r={'screen':str(a.screen),'image_size':im.size,'rectangle':[100,100,64,48],'rgb_bytes':len(rect),'mismatches':sum(a!=b for a,b in zip(expected,rect)),'expected_sha256':hashlib.sha256(expected).hexdigest(),'actual_sha256':hashlib.sha256(rect).hexdigest()}
a.out.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2));raise SystemExit(bool(r['mismatches']))
