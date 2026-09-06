#!/usr/bin/env python3
"""Summarize a verified displayed batch; keep host/guest clocks separate."""
import argparse
import json
import math
from pathlib import Path
import statistics
from present_peer import fields, verify, verify_final

def summary(values):
    ordered=sorted(values)
    return dict(count=len(values),median_us=statistics.median(values),
                p95_us=ordered[math.ceil(.95*len(values))-1],max_us=max(values),
                over_16667_us=sum(x>16667 for x in values),
                over_33333_us=sum(x>33333 for x in values))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('trial',type=Path);a=p.parse_args()
    result=json.loads((a.trial/'result.json').read_text())
    if not result.get('passed'):raise ValueError('trial did not pass its complete runtime acceptance')
    records=[json.loads(x) for x in (a.trial/'driver-host.jsonl').read_text().splitlines()]
    accepted=verify(a.trial,result['events'],records)
    final=verify_final(a.trial,result['events'])
    lines=[e['line'] for e in result['events'] if e.get('source')=='shared-ram-audit']
    one=lambda prefix: fields(next(x for x in lines if x.startswith(prefix)))
    batch=one('GPU_LOAD_PRESENT_BATCH ');setup=one('GPU_LOAD_PRESENT_SETUP ')
    frames=accepted['frames_metadata'];steady=frames[1:]
    draws=[r for r in records if r['op']=='residentDraw']
    timestamps=[x['host_ns'] for x in accepted['scanouts']]
    deltas=[(b-a)/1000 for a,b in zip(timestamps,timestamps[1:])]
    report=dict(scope='repeated resident exact-guest blur, GPU conversion, guest IOSurface delivery and DCP sink; static input, not UI FPS',
        setup_guest_us=float(setup['us']),first_frame=frames[0],
        steady_guest={k:summary([float(x[k]) for x in steady]) for k in ('gpu_us','draw_us','delivery_us','swap_us','total_us')},
        guest_batch_us=float(batch['wall_us']),guest_steady_batch_us=float(batch['steady_wall_us']),
        guest_steady_frames_per_second=32e6/float(batch['steady_wall_us']),
        host_scanout_intervals=summary(deltas),host_scanout_frames_per_second=32e9/(timestamps[-1]-timestamps[0]),
        steady_host_service=summary([r['host_service_us'] for r in draws[1:]]),
        correctness_pass=True,performance_pass_60hz=summary([float(x['total_us']) for x in steady])['p95_us']<16667,
        final_pixels=final,verification_reads_in_batch=0,frames=33,dispatches=99,
        limits='32 steady samples; guest stage clocks and host scanout clock reported separately; no changing UI inputs, vsync pacing, full renderer or live checkpoint claim')
    (a.trial/'displayed-metrics.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
