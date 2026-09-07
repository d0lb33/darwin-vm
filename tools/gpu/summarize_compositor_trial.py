#!/usr/bin/env python3
"""Summarize captured compositor work; never infer steady pacing from startup."""
import argparse
import json
from pathlib import Path


def extent(values):
    return dict(count=len(values), minimum=min(values), maximum=max(values)) if values else None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('run', type=Path)
    a = p.parse_args()
    result = json.loads((a.run/'result.json').read_text())
    rows = [json.loads(line) for line in (a.run/'driver-host.jsonl').read_text().splitlines()]
    gpu = [r for r in rows if r['reply'].get('ok') and 'gpu_us' in r['reply']]
    live = {}
    peaks = dict(objects=0, ordinary_native_allocated_bytes=0, imported_mapped_bytes=0)
    allocations = {'buffer','texture','textureView','linearTexture','surfaceImport','library','function',
                   'pipeline','computePipeline','renderPipeline','sampler','depthState'}
    for r in rows:
        q, reply = r['request'], r['reply']
        if not reply.get('ok'):
            continue
        if r['op'] in allocations and 'handle' in reply:
            live[reply['handle']] = (r['op'], reply.get('allocatedSize',0), reply.get('id'), reply.get('mappedBytes',0))
        elif r['op'] == 'release':
            live.pop(q['handle'],None)
        current = dict(objects=len(live), ordinary_native_allocated_bytes=sum(v[1] for v in live.values() if v[0] in ('texture','buffer')),
                       imported_mapped_bytes=sum(dict((v[2],v[3]) for v in live.values() if v[0]=='surfaceImport').values()))
        for key in peaks:
            peaks[key] = max(peaks[key],current[key])
    samples = []
    if (a.run/'process-memory.jsonl').exists():
        samples = [json.loads(line) for line in (a.run/'process-memory.jsonl').read_text().splitlines()]
    failed = [dict(seq=r['seq'],op=r['op'],reply=r['reply'],commands=len(r['request'].get('commands',[]))) for r in rows if not r['reply'].get('ok')]
    report = dict(scope='bounded boot/startup evidence; not sustained pacing or independent scene semantics',
                  elapsed_boot_s=result['elapsed'], stop_reason=result['stop_reason'], requests=len(rows),
                  native_presentations=result['native_presentations'], native_completions=result['native_completions'],
                  completed_gpu_batches=len(gpu), render_passes=sum(r['reply'].get('renderPasses',0) for r in gpu),
                  compute_dispatches=sum(r['reply'].get('computePasses',0) for r in gpu), draws=sum(r['reply'].get('draws',0) for r in gpu),
                  first_gpu_batch_us=gpu[0]['reply']['gpu_us'] if gpu else None,
                  later_gpu_batch_us=extent([r['reply']['gpu_us'] for r in gpu[1:]]),
                  host_batch_service_us=extent([r['host_service_us'] for r in gpu]),
                  timing_limit='GPU/service times exclude earlier uploads, guest scheduling and display; no end-to-end frame latency claim',
                  observed_peaks=peaks, final_live_objects=len(live),
                  accounting_limit='native sizes for ordinary buffers/textures, aliases excluded; unique imported spans separate; excludes compiler/driver/process overhead',
                  rss=dict(samples=len(samples),qemu_kib=extent([s['qemu_rss_kib'] for s in samples]),worker_kib=extent([s['worker_rss_kib'] for s in samples])),
                  rss_limit='one-second samples after sampler invocation; may miss peaks; not Metal allocation counts or leak proof',
                  failed_requests=failed, sustained_pacing_verified=False, full_lifetime_retirement_verified=False)
    (a.run/'work-summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
