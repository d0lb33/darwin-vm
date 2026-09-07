#!/usr/bin/env python3
"""Decode a captured RGBA16Float allocation; no display-color correctness claim.

Requires numpy/Pillow. Retains the raw bytes; preview clamps linear RGB and
encodes sRGB without exposure normalization. This is not a DCP screenshot.
"""
import argparse,hashlib,json
from pathlib import Path
import numpy as np
from PIL import Image
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('snapshot',type=Path);p.add_argument('id',type=int);p.add_argument('output',type=Path);a=p.parse_args()
    index=json.loads((a.snapshot/'index.json').read_text());entry=next(x for x in index['surfaces'] if x['id']==a.id)
    data=(a.snapshot/entry['file']).read_bytes()
    if hashlib.sha256(data).hexdigest()!=entry['sha256']:raise ValueError('snapshot hash')
    d=entry['descriptors'][-1];w,h,row=d['width'],d['height'],d['row']
    if d['format']!=115 or row%8 or row<w*8 or row*h>len(data):raise ValueError('requires recorded RGBA16Float geometry')
    pixels=np.frombuffer(data[:row*h],dtype='<f2').reshape(h,row//2)[:,:w*4].reshape(h,w,4).astype(np.float32)
    if not np.isfinite(pixels).all():raise ValueError('nonfinite pixels')
    rgb=np.clip(pixels[:,:,:3],0,1);rgb=np.where(rgb<=.0031308,rgb*12.92,1.055*rgb**(1/2.4)-.055)
    if a.output.exists():raise ValueError('output exists')
    Image.fromarray(np.round(rgb*255).astype('uint8')).save(a.output)
    a.output.with_suffix('.json').write_text(json.dumps(dict(scope=__doc__,source=str(a.snapshot.resolve()),id=a.id,
        minimum=pixels.min(axis=(0,1)).tolist(),maximum=pixels.max(axis=(0,1)).tolist(),
        nonzero_rgb=int(np.count_nonzero(pixels[:,:,:3])),raw_sha256=entry['sha256']),indent=2)+'\n')
    print(a.output)
if __name__=='__main__':main()
