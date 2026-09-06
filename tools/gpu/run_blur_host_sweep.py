#!/usr/bin/env python3
"""Bounded whole-image host ceiling, exact AIR and retained image contents."""
import argparse,hashlib,json,subprocess,sys
from pathlib import Path
from blur_peer import SIZES
p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--air',type=Path,required=True);a=p.parse_args()
if hashlib.sha256(a.air.read_bytes()).hexdigest()!='8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364':raise ValueError('AIR SHA')
a.out.mkdir(exist_ok=False);tools=Path(__file__).resolve().parent
cmd=['xcrun','clang','-fobjc-arc','-O2','-Wall','-Wextra','-Werror',str(tools/'blur_host_probe.m'),'-framework','Foundation','-framework','Metal','-o',str(a.out/'blur-probe')]
subprocess.run(cmd,check=True)
(a.out/'plan.json').write_text(json.dumps(dict(sizes=SIZES,frames=17,compile=cmd,air=str(a.air),air_sha256=hashlib.sha256(a.air.read_bytes()).hexdigest(),binary_sha256=hashlib.sha256((a.out/'blur-probe').read_bytes()).hexdigest()),indent=2)+'\n')
for name in ('blur_host_probe.m','blur_host.h','blur_reference.h'):(a.out/name).write_bytes((tools/name).read_bytes())
for w,h in SIZES:
    with (a.out/f'{w}x{h}.jsonl').open('x') as log,(a.out/f'{w}x{h}.stderr').open('x') as err:
        subprocess.run([str(a.out/'blur-probe'),str(a.air),str(w),str(h),'17'],stdout=log,stderr=err,timeout=60,check=True)
print(a.out)
