#!/usr/bin/env python3
"""Exact AIR blur + GPU BGRA conversion into owned mapped RAM; final-only oracle."""
import argparse,hashlib,json,math,re,statistics,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--air',type=Path,required=True);a=p.parse_args()
sha=hashlib.sha256(a.air.read_bytes()).hexdigest()
if sha!='8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364':raise ValueError('exact AIR mismatch')
a.out.mkdir(exist_ok=False);src=Path(__file__).resolve().parent
cmd=['xcrun','clang','-fobjc-arc','-O2','-Wall','-Wextra','-Werror',str(src/'present_host_probe.m'),'-framework','Metal','-framework','Foundation','-o',str(a.out/'present-host-probe')]
subprocess.run(cmd,check=True)
inputs={}
for name in ('run_present_host_probe.py','present_host_probe.m','present_host.h','present_layout.h','present_reference.h','blur_host.h','blur_reference.h'):
 data=(src/name).read_bytes();(a.out/name).write_bytes(data);inputs[name]=hashlib.sha256(data).hexdigest()
run=[str(a.out/'present-host-probe'),str(a.air),str(a.out/'output-ram.bin')]
(a.out/'plan.json').write_text(json.dumps(dict(compile=cmd,run=run,air_sha256=sha,sources=inputs,binary_sha256=hashlib.sha256((a.out/'present-host-probe').read_bytes()).hexdigest()),indent=2)+'\n')
with (a.out/'host-shared-output.txt').open('x') as f:subprocess.run(run,stdout=f,stderr=subprocess.STDOUT,timeout=30,check=True)
text=(a.out/'host-shared-output.txt').read_text()
if text.count('FRAME frame=')!=33 or 'BATCH frames=33 ' not in text or 'FINAL bad_pixels=0' not in text:raise ValueError('incomplete host verification')
frames=re.findall(r'FRAME frame=(\d+) total_us=(\d+) gpu_us=([\d.]+)',text)
if [int(x[0]) for x in frames]!=list(range(1,34)):raise ValueError('host frame order')
def summary(values):
 ordered=sorted(values)
 return dict(median_us=statistics.median(values),p95_us=ordered[math.ceil(.95*len(values))-1],max_us=max(values),over_16667_us=sum(x>16667 for x in values),over_33333_us=sum(x>33333 for x in values))
batch=re.search(r'BATCH frames=33 wall_us=(\d+) steady_wall_us=(\d+)',text)
metrics=dict(scope='host only; no guest transport, IOSurface or display presentation',setup_us=int(re.search(r'SETUP us=(\d+)',text)[1]),first_frame_us=int(frames[0][1]),steady_total=summary([int(x[1]) for x in frames[1:]]),steady_gpu=summary([float(x[2]) for x in frames[1:]]),batch_us=int(batch[1]),steady_batch_us=int(batch[2]),steady_frames_per_second=32e6/int(batch[2]),frames=33,final_bad_pixels=0,verification_reads_in_batch=0)
(a.out/'metrics.json').write_text(json.dumps(metrics,indent=2)+'\n')
print(a.out)
