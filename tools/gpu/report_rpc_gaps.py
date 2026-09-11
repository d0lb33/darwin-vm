#!/usr/bin/env python3
"""Where wall time goes between compositor frames, from a driver-host.jsonl.

Every record carries the host's receive/complete timestamps and service time,
so for one journal this prints, without any guest instrumentation:

* per op: count, host service time, bytes, and the guest-side gap that
  precedes it (previous completion -> this reception: guest decode/encode,
  compositor work, transport wake-up and daemon loop latency);
* per frame (submit to submit): interval, host service, RPC count, bytes the
  guest CRCs;
* growth with session age: the gap between two consecutive tiny RPCs
  (resourceProcess -> resourceProcess) per bucket of records. Constant guest
  work between them means any growth is host-side latency (2026-09-07,
  docs/re/gpu-home-sluggishness-ios27.md).

Use ``--since-seq`` / ``--until-seq`` to bound the analysis to a window.
"""
import argparse
import collections
import json
import statistics
from pathlib import Path


def stats(values):
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    pick = lambda q: ordered[min(n - 1, int(n * q))]
    return dict(count=n, minimum=ordered[0], p50=pick(.5), p90=pick(.9), p99=pick(.99),
                maximum=ordered[-1], mean=statistics.mean(ordered), sum=sum(ordered))


def fmt(s, digits=3):
    if not s:
        return 'n/a'
    return 'n=%d p50=%.*f p90=%.*f max=%.*f' % (s['count'], digits, s['p50'], digits, s['p90'], digits, s['maximum'])


def load(path, since, until):
    records = []
    for line in Path(path).open():
        if not line.strip():
            continue
        r = json.loads(line)
        if (since is None or r['seq'] >= since) and (until is None or r['seq'] <= until):
            records.append(r)
    return records


def analyze(records, bucket=2000):
    out = dict(records=len(records))
    if len(records) < 2:
        return out
    span = (records[-1]['host_completed_ns'] - records[0]['host_received_ns']) / 1e9
    service = [r['host_service_us'] / 1000 for r in records]
    by_op = collections.defaultdict(list)
    gap_by_op = collections.defaultdict(list)
    bytes_by_op = collections.defaultdict(list)
    gaps = []
    for prev, r in zip(records, records[1:]):
        gap = (r['host_received_ns'] - prev['host_completed_ns']) / 1e6
        gaps.append(gap)
        gap_by_op[r['op']].append(gap)
    for r in records:
        by_op[r['op']].append(r['host_service_us'] / 1000)
        bytes_by_op[r['op']].append(r['request_bytes'] + r['reply_bytes'])
    out.update(span_s=span, rpcs_per_second=len(records) / span,
               host_service_ms=stats(service), guest_gap_ms=stats(gaps),
               host_service_fraction=sum(service) / 1000 / span,
               crc_bytes_per_second=sum(r['request_bytes'] + r['reply_bytes'] for r in records) / span,
               per_op={op: dict(count=len(v), service_ms=stats(v), bytes=stats(bytes_by_op[op]),
                                gap_before_ms=stats(gap_by_op[op]))
                       for op, v in sorted(by_op.items(), key=lambda kv: -len(kv[1]))})
    submits = [i for i, r in enumerate(records) if r['op'] in ('renderSubmit', 'submit', 'renderStageCommit')]
    frames = []
    for i, j in zip(submits, submits[1:]):
        seg = records[i + 1:j + 1]
        frames.append(dict(
            interval_ms=(records[j]['host_completed_ns'] - records[i]['host_completed_ns']) / 1e6,
            service_ms=sum(r['host_service_us'] for r in seg) / 1000,
            first_gap_ms=(seg[0]['host_received_ns'] - records[i]['host_completed_ns']) / 1e6,
            rpcs=len(seg), bytes=sum(r['request_bytes'] + r['reply_bytes'] for r in seg)))
    out['per_frame'] = dict(frames=len(frames),
                            interval_ms=stats([f['interval_ms'] for f in frames]),
                            service_ms=stats([f['service_ms'] for f in frames]),
                            submit_to_first_rpc_ms=stats([f['first_gap_ms'] for f in frames]),
                            rpcs=stats([f['rpcs'] for f in frames]),
                            bytes=stats([f['bytes'] for f in frames]))
    growth = collections.defaultdict(lambda: collections.defaultdict(list))
    for prev, r in zip(records, records[1:]):
        gap = (r['host_received_ns'] - prev['host_completed_ns']) / 1e6
        key = None
        if prev['op'] == 'resourceProcess' and r['op'] == 'resourceProcess':
            key = 'rp_to_rp'
        elif prev['op'] == 'writeRenderBuffer' and r['op'] == 'writeRenderBuffer':
            key = 'wrb_to_wrb'
        elif prev['op'] == 'renderSubmit' and r['op'] == 'resourceProcess':
            key = 'submit_to_rp'
        elif prev['op'] == 'writeRenderBuffer' and r['op'] == 'renderSubmit':
            key = 'wrb_to_submit'
        elif prev['op'] == 'writeTextureChunk' and r['op'] == 'writeTextureChunk' and \
                prev['request'].get('texture') == r['request'].get('texture'):
            key = 'chunk_to_chunk'
        if key:
            growth[r['seq'] // bucket * bucket][key].append(gap)
        growth[r['seq'] // bucket * bucket]['service_' + r['op']].append(r['host_service_us'] / 1000)
    out['growth'] = {str(k): {name: stats(v) for name, v in row.items()} for k, row in sorted(growth.items())}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('journal', type=Path)
    ap.add_argument('--since-seq', type=int)
    ap.add_argument('--until-seq', type=int)
    ap.add_argument('--bucket', type=int, default=2000)
    ap.add_argument('--json', type=Path, help='write the full analysis here')
    a = ap.parse_args()
    records = load(a.journal, a.since_seq, a.until_seq)
    result = analyze(records, a.bucket)
    if a.json:
        a.json.write_text(json.dumps(result, indent=2) + '\n')
    print('%s: %d records' % (a.journal, result['records']))
    if result['records'] < 2:
        return
    print('span %.1f s, %.1f RPC/s, host service %.1f%% of span, guest-side gaps %.1f%%, %.0f CRC bytes/s' % (
        result['span_s'], result['rpcs_per_second'], 100 * result['host_service_fraction'],
        100 * result['guest_gap_ms']['sum'] / 1000 / result['span_s'], result['crc_bytes_per_second']))
    print('%-22s %6s  %-42s %-42s %s' % ('op', 'count', 'host service ms', 'gap before ms (guest side)', 'bytes p50'))
    for op, v in result['per_op'].items():
        print('%-22s %6d  %-42s %-42s %d' % (op, v['count'], fmt(v['service_ms']), fmt(v['gap_before_ms']), v['bytes']['p50']))
    f = result['per_frame']
    print('per frame (submit to submit): interval %s | host service %s | submit->first RPC %s | RPCs %s | bytes %s' % (
        fmt(f['interval_ms'], 1), fmt(f['service_ms'], 2), fmt(f['submit_to_first_rpc_ms'], 1), fmt(f['rpcs'], 0), fmt(f['bytes'], 0)))
    print('growth by %d-record bucket: p50 ms(n) of rp->rp, wrb->wrb, wrb->submit, submit->rp, chunk->chunk gaps; resourceProcess service' % a.bucket)
    for k, row in result['growth'].items():
        cells = []
        for name in ('rp_to_rp', 'wrb_to_wrb', 'wrb_to_submit', 'submit_to_rp', 'chunk_to_chunk', 'service_resourceProcess'):
            s = row.get(name)
            cells.append('%8.3f(%4d)' % (s['p50'], s['count']) if s else '%14s' % '-')
        print('  seq>=%6s  %s' % (k, '  '.join(cells)))


if __name__ == '__main__':
    main()
