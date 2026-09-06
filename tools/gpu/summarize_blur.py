#!/usr/bin/env python3
"""Reverify a completed blur guest run and report observed costs, not display FPS."""
import argparse,json,re,statistics,math
from pathlib import Path
import blur_peer

def dist(values):
    s=sorted(values)
    return dict(n=len(s),min=min(s),p50=statistics.median(s),p95=s[math.ceil(.95*len(s))-1],max=max(s),over_16_667_ms=sum(v>16667 for v in s),over_33_333_ms=sum(v>33333 for v in s))

def summarize(root):
    r=json.loads((root/'result.json').read_text());records=[json.loads(x) for x in (root/'driver-host.jsonl').read_text().splitlines()]
    if not r.get('passed') or not r.get('driver_mmio') or r.get('debugger') or r.get('ram_restored'):raise ValueError('not a passed fresh MMIO run')
    launch=json.loads((root/'launch.json').read_text());argv=launch['argv'];env=launch['env']
    if any(x in argv for x in ('-gdb','-s','-S','-incoming','-loadvm')) or 'DARWIN_ANS_AUX_DRIVE' in env:raise ValueError('debugger/restore/NVMe auxiliary transport')
    drives=[argv[i+1] for i,x in enumerate(argv) if x=='-drive']
    if len(drives)!=1 or not drives[0].startswith('if=none,id=ans,'):raise ValueError('unexpected disk transport')
    verified=blur_peer.verify(root,r['events'],records)
    parsed=[dict(re.findall(r'(\w+)=([^ ]+)',e['line']),kind=e['line'].split()[0]) for e in r['events'] if e['line'].startswith('GPU_LOAD_BLUR_')]
    sizes=[]
    for w,h in blur_peer.SIZES:
        rows=[x for x in parsed if x.get('width')==str(w) and x.get('height')==str(h)]
        cpu=next(x for x in rows if x['kind']=='GPU_LOAD_BLUR_CPU');batch=next(x for x in rows if x['kind']=='GPU_LOAD_BLUR_BATCH');frames=[x for x in rows if x['kind']=='GPU_LOAD_BLUR_FRAME']
        cpu_work=list(map(int,cpu['work_us'].split(',')));cpu_output=list(map(int,cpu['output_us'].split(',')))
        if len(cpu_work)!=17 or len(cpu_output)!=17 or [int(f['run']) for f in frames]!=list(range(17)):raise ValueError('sample count/order')
        gpu=[float(f['total_us']) for f in frames];totals=[a+b for a,b in zip(cpu_work,cpu_output)]
        sizes.append(dict(width=w,height=h,first_cpu_us=totals[0],first_gpu_us=gpu[0],cpu_total=dist(totals[1:]),gpu_total=dist(gpu[1:]),cpu_kernel=dist(cpu_work[1:]),cpu_delivery=dist(cpu_output[1:]),gpu_path=dist([float(f['gpu_path_us']) for f in frames[1:]]),gpu_delivery=dist([float(f['output_us']) for f in frames[1:]]),rpc=dist([float(f['rpc_us']) for f in frames[1:]]),doorbell=dist([float(f['doorbell_us']) for f in frames[1:]]),verification=dist([float(f['verify_us']) for f in frames[1:]]),observed_batch_frames_per_second=17e6/float(batch['wall_us']),service_time_reciprocal_per_second=16e6/sum(gpu[1:]),cpu_to_gpu_median_ratio=statistics.median(totals[1:])/statistics.median(gpu[1:]),samples=frames,cpu_samples=cpu_work,cpu_output_samples=cpu_output))
    return dict(verified=verified,sizes=sizes,request_bytes=sum(x['request_bytes'] for x in records),reply_bytes=sum(x['reply_bytes'] for x in records),rpc_count=len(records),limitations=['offscreen owned IOSurface, no blur scanout or Liquid Glass','16 follow-on samples per size, not production p99','observed batch throughput includes verification/logging; service reciprocal is not observed display FPS','GPU tiled, CPU separable whole-image; same output semantics, different overhead','no GPU checkpoint proof'])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args();v=summarize(a.run)
    with a.output.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
    for s in v['sizes']:print(s['width'],s['height'],'CPU',s['cpu_total']['p50'],'GPU',s['gpu_total']['p50'],'ratio',s['cpu_to_gpu_median_ratio'])
