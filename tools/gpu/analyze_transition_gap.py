#!/usr/bin/env python3
"""Offline attribution of an ordered presentation interval; no VM or GPU execution."""
import argparse
import base64
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from report_compositor_pacing import stats


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('run', type=Path)
    p.add_argument('--after-thumbnail', type=int, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(exist_ok=False)
    presentations = json.loads((a.run/'presentation-pacing.json').read_text())['frames']
    thumbnails = json.loads((a.run/'home-transition.json').read_text())['frames']
    rows = [json.loads(s) for s in (a.run/'driver-host.jsonl').read_text().splitlines()]
    gpu = [r for r in rows if r['reply'].get('ok') and 'gpu_us' in r['reply']]
    if len(gpu) != len(presentations):
        raise ValueError('cannot infer ordered association: GPU/display counts differ')
    thumbnail = next(f for f in thumbnails if f['number'] == a.after_thumbnail)
    i = next(i for i,f in enumerate(presentations) if f['present_ns'] == thumbnail['present_ns'])
    left, right = gpu[i:i+2]
    first, last = presentations[i:i+2]
    selected = [r for r in rows if left['seq'] < r['seq'] <= right['seq']]
    gaps, uploads = [], defaultdict(bytearray)
    previous = left['host_completed_ns']
    for r in selected:
        gaps.append(dict(before_seq=r['seq'], op=r['op'],
                         interval_ms=(r['host_received_ns']-previous)/1e6,
                         service_ms=r['host_service_us']/1000))
        previous = r['host_completed_ns']
        if r['op'] == 'writeTextureChunk':
            raw = (a.run/r['upload_file']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != r['upload_sha256']:
                raise ValueError('upload evidence hash mismatch')
            q = json.loads(raw)
            if q['offset'] != len(uploads[q['texture']]):
                raise ValueError('upload does not start at zero or has missing chunks')
            uploads[q['texture']].extend(base64.b64decode(q['data'], validate=True))
    descriptors = {r['reply']['handle']:r['request'] for r in rows
                   if r['op']=='texture' and r['reply'].get('ok')}
    texture_report = []
    for handle, data in uploads.items():
        descriptor = descriptors[handle]
        (a.out/f'texture-{handle}.texels').write_bytes(data)
        texture_report.append(dict(handle=handle, descriptor=descriptor,
                                   bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    host_gap = (right['host_completed_ns']-left['host_completed_ns'])/1e6
    display_gap = (last['present_ns']-first['present_ns'])/1e6
    # Compare intervals only. QEMU CLOCK_MONOTONIC and Python's clock have
    # different absolute origins in this capture; never subtract those origins.
    discrepancies = [((q['present_ns']-p['present_ns'])-
                      (b['host_completed_ns']-a['host_completed_ns']))/1e6
                     for p,q,a,b in zip(presentations,presentations[1:],gpu,gpu[1:])]
    report = dict(scope=__doc__, presentation_indices=[i,i+1],
                  association='inferred by complete ordered streams; includes renderStageCommit as a GPU batch; no shared per-frame ID',
                  request_sequences=[left['seq'],right['seq']],
                  display_interval_ms=display_gap, host_completion_interval_ms=host_gap,
                  display_completion_ms=[(f['complete_ns']-f['present_ns'])/1e6 for f in (first,last)],
                  host_service_ms=sum(r['host_service_us'] for r in selected)/1000,
                  between_recorded_requests_ms=sum(g['interval_ms'] for g in gaps),
                  rpc_count=len(selected), operations=dict(Counter(r['op'] for r in selected)),
                  final_gpu_ms=right['reply']['gpu_us']/1000,
                  first_rpc_gap_ms=gaps[0]['interval_ms'],
                  largest_between_request_gaps=sorted(gaps,key=lambda g:g['interval_ms'],reverse=True)[:12],
                  interval_discrepancies_ms=stats(discrepancies),
                  textures=texture_report, uploaded_texture_bytes=sum(map(len,uploads.values())),
                  unresolved='Inter-request gaps combine guest preparation/TCG scheduling, notification delivery, host polling and post-reply evidence I/O; this capture cannot separate them. No full-scene replay or causal timing proof.')
    (a.out/'analysis.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('textures','largest_between_request_gaps')},indent=2))


if __name__ == '__main__':
    main()
