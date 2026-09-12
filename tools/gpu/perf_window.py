#!/usr/bin/env python3
"""Measure one bounded window of a live backboardd GPU session (session_cli).

Written 2026-09-07 to answer "what makes the accelerated home screen sluggish:
the Metal driver, its transport, or TCG?". Everything is read-only for the
guest except the optional HMP ``stop``/``cont`` pair used to sample vCPU PCs.

Over ``--seconds`` it records, for the session's ``run/`` directory:

* host CPU: QEMU per-thread CPU-seconds (``ps -M``), the session daemon and
  the Metal worker (``driver_host``) CPU-seconds, optional macOS ``sample`` of
  QEMU (``--host-sample N``);
* guest/transport, from the same window of the session's own logs:
  presentations and inter-presentation intervals (``stderr.log``
  ``iomfb: presented ... monotonic_ns=``), and every RPC in
  ``driver-host.jsonl`` (count per op, host service time, guest-side gaps
  between consecutive RPCs, request/reply bytes the guest CRCs);
* optional vCPU PC samples classified kernel / shared cache / user image.

Output: ``<out>/result.json`` plus the raw sample text. ``--report`` prints a
summary. Never sends input and never writes to the guest.
"""
import argparse
import collections
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 're'))
from idle_host_profile import thread_times, vcpu_sample, classify, hmp  # noqa: E402

PRESENTED = re.compile(r'^iomfb: presented .*?monotonic_ns=(\d+)', re.M)
PRESENTED_DETAIL = re.compile(
    r'^iomfb: presented .*?scanout_us=(\d+) dma_us=(\d+) '
    r'convert_us=(\d+) console_us=(\d+) monotonic_ns=(\d+)', re.M)
COMPLETED = re.compile(r'^iomfb: swap id \d+ D594 (?:nested )?completed, status 0x([0-9a-f]+).*?monotonic_ns=(\d+)', re.M)
DISPLAY_POWER = re.compile(r'^iomfb: A484 display power (\d) -> (\d) ', re.M)


def stats(values):
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    pick = lambda q: ordered[min(n - 1, int(n * q))]
    return dict(count=n, minimum=round(ordered[0], 3), p50=round(pick(.5), 3), p90=round(pick(.9), 3),
                p99=round(pick(.99), 3), maximum=round(ordered[-1], 3), mean=round(statistics.mean(ordered), 3),
                sum=round(sum(ordered), 3))


def cputime_seconds(pid):
    out = subprocess.run(['ps', '-o', 'cputime=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
    if not out:
        return None
    total = 0.0
    for part in out.split(':'):
        total = total * 60 + float(part)
    return total


def find_daemon(session):
    out = subprocess.run(['pgrep', '-f', 'session_cli.py serve'], capture_output=True, text=True).stdout.split()
    for pid in out:
        cmd = subprocess.run(['ps', '-o', 'command=', '-p', pid], capture_output=True, text=True).stdout
        if ('--session ' + str(session)) in cmd:
            return int(pid)
    return None


def find_worker(daemon):
    if daemon is None:
        return None
    out = subprocess.run(['pgrep', '-P', str(daemon), '-f', 'driver_host'], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else None


def complete_lines(path, offset):
    """Return (text of complete lines after offset, new offset)."""
    with open(path, 'rb') as f:
        f.seek(offset)
        chunk = f.read()
    cut = chunk.rfind(b'\n')
    if cut < 0:
        return '', offset
    return chunk[:cut + 1].decode(errors='replace'), offset + cut + 1


def presentation_stats(text):
    present = [int(m.group(1)) for m in PRESENTED.finditer(text)]
    complete = [(int(m.group(2)), int(m.group(1), 16)) for m in COMPLETED.finditer(text)]
    intervals = [(b - a) / 1e6 for a, b in zip(present, present[1:])]
    power = [(int(m.group(1)), int(m.group(2))) for m in DISPLAY_POWER.finditer(text)]
    span = (present[-1] - present[0]) / 1e9 if len(present) > 1 else 0
    return dict(presentations=len(present), completions=len(complete),
                completion_failures=sum(1 for _, s in complete if s),
                span_s=round(span, 3), per_second=round(len(present) / span, 2) if span else None,
                inter_presentation_ms=stats(intervals), display_power_transitions=power,
                intervals_ms=[round(x, 1) for x in intervals])


def rpc_stats(text):
    records = [json.loads(line) for line in text.splitlines() if line.strip()]
    by_op = collections.defaultdict(list)
    gaps_by_op = collections.defaultdict(list)
    gaps = []
    bytes_total = 0
    for prev, rec in zip([None] + records, records):
        by_op[rec['op']].append(rec['host_service_us'] / 1000)
        bytes_total += rec['request_bytes'] + rec['reply_bytes']
        if prev is not None:
            gap = (rec['host_received_ns'] - prev['host_completed_ns']) / 1e6
            gaps.append(gap)
            gaps_by_op[rec['op']].append(gap)
    service = [r['host_service_us'] / 1000 for r in records]
    span = (records[-1]['host_completed_ns'] - records[0]['host_received_ns']) / 1e9 if len(records) > 1 else 0
    gpu = [r['reply']['gpu_us'] / 1000 for r in records if isinstance(r.get('reply'), dict) and r['reply'].get('gpu_us') is not None]
    submits = [i for i, r in enumerate(records) if r['op'] in ('renderSubmit', 'submit', 'renderStageCommit')]
    per_frame = []
    for i, j in zip(submits, submits[1:]):
        seg = records[i + 1:j + 1]
        per_frame.append(dict(
            interval_ms=(records[j]['host_completed_ns'] - records[i]['host_completed_ns']) / 1e6,
            service_ms=sum(r['host_service_us'] for r in seg) / 1000,
            rpcs=len(seg),
            bytes=sum(r['request_bytes'] + r['reply_bytes'] for r in seg)))
    return dict(rpcs=len(records), span_s=round(span, 3),
                rpcs_per_second=round(len(records) / span, 2) if span else None,
                host_service_ms=stats(service), guest_gap_ms=stats(gaps),
                host_service_fraction_of_span=round(sum(service) / 1000 / span, 4) if span else None,
                crc_bytes_total=bytes_total,
                crc_bytes_per_second=round(bytes_total / span) if span else None,
                gpu_ms=stats(gpu),
                per_op={op: dict(count=len(v), service_ms=stats(v), gap_before_ms=stats(gaps_by_op[op]))
                        for op, v in sorted(by_op.items(), key=lambda kv: -len(kv[1]))},
                per_frame=dict(frames=len(per_frame),
                               interval_ms=stats([p['interval_ms'] for p in per_frame]),
                               service_ms=stats([p['service_ms'] for p in per_frame]),
                               rpcs=stats([p['rpcs'] for p in per_frame]),
                               bytes=stats([p['bytes'] for p in per_frame])))


def render_to_display_stats(stderr_text, journal_text):
    """Correlate host completion with scanout without mixing macOS clocks."""
    presentations = sorted(tuple(map(int, m.groups())) for m in PRESENTED_DETAIL.finditer(stderr_text))
    records = [json.loads(line) for line in journal_text.splitlines() if line.strip()]
    submits = [r for r in records if r.get('op') in ('renderSubmit', 'submit', 'renderStageCommit')
               and isinstance(r.get('reply'), dict) and r['reply'].get('ok')
               and 'qemu_clock_received_ns' in r and 'qemu_clock_reply_ready_ns' in r
               and 'qemu_clock_notification_sent_ns' in r]
    submits.sort(key=lambda r: r['qemu_clock_reply_ready_ns'])
    if not presentations or not submits:
        return dict(available=False, reason='needs detailed scanout and QEMU-clock host timestamps')
    ready_to_scanout, notified_to_scanout, ready_to_present, present_to_next = [], [], [], []
    scanout, dma, convert, console = [], [], [], []
    next_submit = 0
    for scanout_us, dma_us, convert_us, console_us, presented in presentations:
        scanout_started = presented - scanout_us * 1000
        candidate = None
        while next_submit < len(submits) and submits[next_submit]['qemu_clock_reply_ready_ns'] <= scanout_started:
            candidate = next_submit
            next_submit += 1
        if candidate is None:
            continue
        reply_ready = submits[candidate]['qemu_clock_reply_ready_ns']
        notified = submits[candidate]['qemu_clock_notification_sent_ns']
        ready_to_scanout.append((scanout_started - reply_ready) / 1e6)
        notified_to_scanout.append((scanout_started - notified) / 1e6)
        ready_to_present.append((presented - reply_ready) / 1e6)
        scanout.append(scanout_us / 1000)
        dma.append(dma_us / 1000)
        convert.append(convert_us / 1000)
        console.append(console_us / 1000)
        if candidate + 1 < len(submits):
            present_to_next.append((submits[candidate + 1]['qemu_clock_received_ns'] - presented) / 1e6)
    return dict(available=True, paired=len(ready_to_present),
                presentations=len(presentations), render_submissions=len(submits),
                reply_ready_to_scanout_start_ms=stats(ready_to_scanout),
                notification_sent_to_scanout_start_ms=stats(notified_to_scanout),
                reply_ready_to_present_ms=stats(ready_to_present),
                presentation_to_next_render_received_ms=stats(present_to_next),
                scanout_ms=stats(scanout), dma_ms=stats(dma),
                convert_ms=stats(convert), console_ms=stats(console))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--session', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--label', default='')
    ap.add_argument('--seconds', type=float, default=30)
    ap.add_argument('--host-sample', type=float, default=0, help='run macOS `sample` on QEMU for N seconds')
    ap.add_argument('--vcpu-interval', type=float, default=0, help='seconds between vCPU PC samples; 0 disables')
    ap.add_argument('--report', action='store_true')
    a = ap.parse_args()
    run = a.session.resolve() / 'run'
    qemu = int((run / 'qemu.pid').read_text().split()[0])
    daemon = find_daemon(a.session.resolve())
    worker = find_worker(daemon)
    a.out.mkdir(parents=True, exist_ok=False)
    stderr_path, journal_path = run / 'stderr.log', run / 'driver-host.jsonl'
    err0 = stderr_path.stat().st_size
    jr0 = journal_path.stat().st_size if journal_path.exists() else 0
    t0 = time.time()
    wall0 = time.monotonic()
    threads0 = thread_times(qemu)
    cpu0 = dict(qemu=cputime_seconds(qemu), daemon=cputime_seconds(daemon) if daemon else None,
                worker=cputime_seconds(worker) if worker else None)
    sample_proc = None
    if a.host_sample:
        sample_proc = subprocess.Popen(
            ['sample', str(qemu), str(int(a.host_sample)), '1', '-mayDie', '-file', str(a.out / 'host-sample.txt')],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    vcpu = []
    monitor = run / 'monitor.sock'
    with (a.out / 'vcpu-samples.txt').open('w') as f:
        while time.monotonic() - wall0 < a.seconds:
            if a.vcpu_interval > 0:
                try:
                    cpus = vcpu_sample(monitor)
                except Exception as e:
                    f.write('%.3f error %s\n' % (time.monotonic() - wall0, e))
                    time.sleep(a.vcpu_interval)
                    continue
                vcpu.append(cpus)
                for c in cpus:
                    pc = c.get('pc', 0)
                    f.write('%.3f cpu%d %s 0x%x %s\n' % (time.monotonic() - wall0, c['cpu'], c.get('el', '?'), pc, classify(pc)))
                f.flush()
                time.sleep(a.vcpu_interval)
            else:
                time.sleep(min(0.5, a.seconds))
    elapsed = time.monotonic() - wall0
    threads1 = thread_times(qemu)
    cpu1 = dict(qemu=cputime_seconds(qemu), daemon=cputime_seconds(daemon) if daemon else None,
                worker=cputime_seconds(worker) if worker else None)
    if sample_proc:
        sample_proc.wait()
    err_text, err1 = complete_lines(stderr_path, err0)
    jr_text, jr1 = complete_lines(journal_path, jr0) if journal_path.exists() else ('', jr0)
    (a.out / 'stderr-window.log').write_text(err_text)
    (a.out / 'driver-host-window.jsonl').write_text(jr_text)

    per_thread = []
    for i, (t1, stat, pct) in threads1.items():
        t0v = threads0.get(i, (t1, stat, pct))[0]
        per_thread.append(dict(index=i, cpu_seconds=round(t1 - t0v, 3), stat=stat))
    per_thread.sort(key=lambda r: -r['cpu_seconds'])
    per_cpu = collections.defaultdict(collections.Counter)
    top = collections.defaultdict(collections.Counter)
    for cpus in vcpu:
        for c in cpus:
            pc = c.get('pc', 0)
            per_cpu[c['cpu']][classify(pc)] += 1
            top[c['cpu']][hex(pc)] += 1
    top_insns = {}
    if vcpu:
        try:
            hmp(monitor, 'stop')
            for cpu, counter in top.items():
                for pc, _ in counter.most_common(3):
                    hmp(monitor, 'cpu %d' % cpu)
                    top_insns[pc] = hmp(monitor, 'x/1i %s' % pc).strip().split('\n')[-1].strip()
            hmp(monitor, 'cpu 0')
        finally:
            hmp(monitor, 'cont')
    delta = {k: round(cpu1[k] - cpu0[k], 3) if cpu0.get(k) is not None and cpu1.get(k) is not None else None for k in cpu0}
    result = dict(
        label=a.label, session=str(a.session), started_unix=t0, elapsed_s=round(elapsed, 3),
        pids=dict(qemu=qemu, daemon=daemon, worker=worker),
        host_cpu_seconds=delta,
        host_cpu_pct={k: round(100 * v / elapsed, 1) if v is not None else None for k, v in delta.items()},
        qemu_threads=per_thread,
        offsets=dict(stderr=[err0, err1], journal=[jr0, jr1]),
        presentations=presentation_stats(err_text),
        rpc=rpc_stats(jr_text) if jr_text.strip() else None,
        render_to_display=render_to_display_stats(err_text, jr_text),
        vcpu_samples=len(vcpu),
        vcpu_classes={str(k): dict(v) for k, v in per_cpu.items()},
        vcpu_top_pcs={str(k): [(pc, n, top_insns.get(pc, '')) for pc, n in v.most_common(5)] for k, v in top.items()},
        host_sample=str(a.out / 'host-sample.txt') if a.host_sample else None)
    (a.out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    if a.report:
        p = result['presentations']
        print('%s: %.1f s window' % (a.label or 'window', elapsed))
        print('host CPU %%: qemu %s (threads: %s), daemon %s, worker %s' % (
            result['host_cpu_pct']['qemu'],
            [(r['index'], r['cpu_seconds']) for r in per_thread[:8]],
            result['host_cpu_pct']['daemon'], result['host_cpu_pct']['worker']))
        print('presentations %d (%s/s) intervals ms %s power %s' % (
            p['presentations'], p['per_second'], p['inter_presentation_ms'], p['display_power_transitions']))
        r = result['rpc']
        if r:
            print('rpcs %d (%s/s) host service %s ms, fraction of span %s, guest gaps %s ms, crc bytes/s %s' % (
                r['rpcs'], r['rpcs_per_second'], r['host_service_ms'] and r['host_service_ms']['sum'],
                r['host_service_fraction_of_span'], r['guest_gap_ms'] and r['guest_gap_ms']['sum'], r['crc_bytes_per_second']))
            for op, v in list(r['per_op'].items())[:8]:
                print('   %-22s n=%-5d service p50 %.3f ms  gap-before p50 %s ms' % (
                    op, v['count'], v['service_ms']['p50'], v['gap_before_ms'] and v['gap_before_ms']['p50']))
            print('   per frame: %s' % r['per_frame'])
        timing = result['render_to_display']
        if timing['available']:
            print('render/display pairs %d: reply-ready->scanout %s ms, scanout %s ms, present->next render %s ms' % (
                timing['paired'], timing['reply_ready_to_scanout_start_ms'], timing['scanout_ms'],
                timing['presentation_to_next_render_received_ms']))
        for cpu in sorted(per_cpu):
            print('cpu%d classes=%s' % (cpu, dict(per_cpu[cpu])))
            for pc, n, insn in result['vcpu_top_pcs'][str(cpu)][:3]:
                print('    %s x%d  %s' % (pc, n, insn))
    print('wrote', a.out / 'result.json')


if __name__ == '__main__':
    main()
