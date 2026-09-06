#!/usr/bin/env python3
"""Condition-bounded JSON/binary ABBA comparison on isolated disk children."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
from driver_binary import decode_request,decode_reply

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--control-manifest',type=Path,required=True)
p.add_argument('--binary-manifest',type=Path,required=True)
p.add_argument('--control-build',type=Path,required=True)
p.add_argument('--binary-build',type=Path,required=True)
p.add_argument('--tag',required=True)
a=p.parse_args()
if not a.tag.isalnum() or len(a.tag)>30:raise ValueError('short alphanumeric tag required')
out=Path('/tmp/dvm')/a.tag;out.mkdir(exist_ok=False)
tools=Path(__file__).resolve().parent
result=dict(order=['A1','B1','B2','A2'],target_reduction=.25,runs=[],passed=False)
(out/'plan.json').write_text(json.dumps(dict(arguments={k:str(v) for k,v in vars(a).items()},**result),indent=2)+'\n')
try:
    for label in result['order']:
        binary=label.startswith('B');manifest=a.binary_manifest if binary else a.control_manifest
        build=a.binary_build if binary else a.control_build;tag=a.tag+label;run=Path('/tmp/dvm')/tag
        cmd=[sys.executable,str(tools/'run_guest_load.py'),str(manifest),'--tag',tag,'--seconds','180',
            '--driver-mmio','--driver-wait-display','--driver-worker',str(build/'driver_host'),
            '--library-cache','/tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib']
        print(json.dumps(dict(trial=label,command=cmd)),flush=True)
        with (out/(label+'.log')).open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
        with (out/(label+'-verify.log')).open('x') as log:
            subprocess.run([sys.executable,str(tools/'verify_mmio_metal.py'),str(run),'--output',str(run/'verification.json')],stdout=log,stderr=subprocess.STDOUT,check=True)
        v=json.loads((run/'verification.json').read_text())
        records=[json.loads(line) for line in (run/'driver-host.jsonl').read_text().splitlines()]
        submits=[r for r in records if r['op']=='submit']
        if len(submits)!=8:raise ValueError('missing submissions')
        for r in submits:
            if r['wire_encoding']!=('binary-v1' if binary else 'json'):raise ValueError('wrong comparison mode')
            if binary:
                req=(run/f"binary-request-{r['seq']:04d}.bin").read_bytes()
                reply=(run/f"binary-reply-{r['seq']:04d}.bin").read_bytes()
                if decode_request(req)!=r['request'] or decode_reply(reply)!=r['reply']:raise ValueError('binary wire/evidence disagreement')
        row=dict(label=label,run=str(run),binary=binary,timings_us=v['timings_us'],requests=v['requests'],request_bytes=v['request_bytes'],reply_bytes=v['reply_bytes'])
        result['runs'].append(row);print(json.dumps(row),flush=True)
    control=statistics.mean(r['timings_us']['work']['p50'] for r in result['runs'] if not r['binary'])
    result['control_mean_of_medians_us']=control
    result['binary_reductions']=[1-r['timings_us']['work']['p50']/control for r in result['runs'] if r['binary']]
    result['target_met']=all(x>=.25 for x in result['binary_reductions'])
    result['passed']=True
finally:
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
