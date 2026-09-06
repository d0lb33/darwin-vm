#!/usr/bin/env python3
"""Summarize a verified displayed batch; keep host/guest clocks separate."""
import argparse
import json
import math
from pathlib import Path
import statistics
from present_peer import configuration, fields, verify, verify_final

def summary(values):
    ordered=sorted(values)
    return dict(count=len(values),median_us=statistics.median(values),
                p95_us=ordered[math.ceil(.95*len(values))-1],p99_us=ordered[math.ceil(.99*len(values))-1],max_us=max(values),
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
    config=configuration(a.trial);count=config['frames'];hz=config['hz']
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
        guest_steady_frames_per_second=(count-1)*1e6/float(batch['steady_wall_us']),
        host_scanout_intervals=summary(deltas),host_scanout_frames_per_second=(count-1)*1e9/(timestamps[-1]-timestamps[0]),
        steady_host_service=summary([r['host_service_us'] for r in draws[1:]]),
        correctness_pass=True,
        final_pixels=final,verification_reads_in_batch=0,frames=count,dispatches=count*3,
        limits='Guest stage clocks and host DCP sink clock reported separately; static input; pacing is a guest timer, not physical vsync; no full renderer or live checkpoint claim')
    if hz:
        period=1e6/hz
        misses=sum(x['finish_us']>x['target_us']+period for x in steady)
        skipped=int(batch['skipped'])
        report['pacing']=dict(target_hz=hz,skipped_targets=skipped,completion_deadline_misses=misses,
            target_slots=count-1+skipped,
            start_lateness=summary([max(0,x['start_us']-x['target_us']) for x in steady]),
            completion_from_target=summary([x['finish_us']-x['target_us'] for x in steady]),
            host_intervals_over_1_5_periods=sum(x>period*1.5 for x in deltas),
            host_intervals_over_2_periods=sum(x>period*2 for x in deltas),
            strict_no_missed_deadlines=(skipped==0 and misses==0))
    (a.trial/'displayed-metrics.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
