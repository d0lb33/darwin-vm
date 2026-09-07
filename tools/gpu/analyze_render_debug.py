#!/usr/bin/env python3
"""Decode bounded replay snapshots; never promote them to guest acceptance.

Eight-bit color PNGs preserve texels. Half-float previews clamp to [0,1]; the
original .texels and numerical ranges preserve signed/HDR values separately.
"""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import struct
from analyze_uikit_capture import write_png


def analyze(directory):
    reports=[]
    for path in sorted(directory.glob('capture-*.json')):
        r=json.loads(path.read_text());fmt=r['format'];bpp={1:1,10:1,30:2,70:4,80:4,115:8}[fmt]
        w,h,row=r['width'],r['height'],r['row'];name=r['file']
        if Path(name).name!=name or not 0<w<=4096 or not 0<h<=4096 or row<w*bpp:raise ValueError('snapshot extent/name')
        raw=(directory/name).read_bytes()
        if len(raw)!=row*h or len(raw)>16*1024*1024:raise ValueError('snapshot byte length')
        packed=b''.join(raw[y*row:y*row+w*bpp] for y in range(h))
        pixels=list(struct.iter_unpack('<4e' if fmt==115 else '<'+str(bpp)+'B',packed))
        finite=[p for p in pixels if all(math.isfinite(c) for c in p)]
        report={**r,'sha256':hashlib.sha256(raw).hexdigest(),'packed_sha256':hashlib.sha256(packed).hexdigest(),
            'nonfinite_pixels':len(pixels)-len(finite),'unique_pixels':len(set(pixels)),
            'minimum':[min(p[c] for p in finite) for c in range(len(pixels[0]))] if finite else None,
            'maximum':[max(p[c] for p in finite) for c in range(len(pixels[0]))] if finite else None,
            'most_common':[dict(value=list(p),count=count) for p,count in collections.Counter(finite).most_common(4)]}
        if fmt in (70,80,115):
            if fmt==115:
                bgra=bytes(round(max(0,min(1,p[c]))*255) if math.isfinite(p[c]) else 0 for p in pixels for c in (2,1,0,3))
            elif fmt==70:bgra=bytes(p[c] for p in pixels for c in (2,1,0,3))
            else:bgra=packed
            preview=path.with_suffix('.png');write_png(preview,bgra,w,h)
            report.update(preview=preview.name,preview_conversion='clamped-half-float-not-raw-output' if fmt==115 else 'lossless-byte-channel-conversion')
        reports.append(report)
    if not reports:raise ValueError('no diagnostic snapshots')
    result=dict(scope='instrumented-host-replay-texture-diagnostics-not-guest-or-performance-acceptance',captures=reports)
    (directory/'analysis.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);a=p.parse_args()
    result=analyze(a.directory)
    print(json.dumps(dict(captures=len(result['captures']),bytes=sum(r['bytes'] for r in result['captures']),scope=result['scope'])))
