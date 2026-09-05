#!/usr/bin/env python3
"""Alternate two QEMU binaries through the diskless six-CPU TLBI matrix."""
import argparse
import json
from pathlib import Path
import signal
import statistics
import subprocess
import sys


def terminate(signum, frame):
    raise SystemExit(128 + signum)


signal.signal(signal.SIGTERM, terminate)
ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--baseline', type=Path, required=True)
ap.add_argument('--candidate', type=Path, required=True)
ap.add_argument('--out', type=Path, required=True)
ap.add_argument('--repeat', type=int, default=5)
a = ap.parse_args()
if not 1 <= a.repeat <= 10:
    ap.error('repeat must be in [1, 10]')
a.out.mkdir(exist_ok=False)
report = {'runs': [], 'medians': {}}
for rep in range(a.repeat):
    order = ['baseline', 'candidate'] if rep % 2 == 0 else ['candidate', 'baseline']
    for variant in order:
        out = a.out / f'{rep}_{variant}'
        cmd = [sys.executable, str(Path(__file__).with_name('tlb_broadcast_check.py')),
               '--qemu', str(getattr(a, variant).resolve()), '--out', str(out)]
        with (a.out / f'{rep}_{variant}.log').open('w') as log:
            child = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
            try:
                code = child.wait(timeout=120)
                if code:
                    raise RuntimeError(f'{variant} check failed; see {log.name}')
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=15)
        result = json.loads((out / 'results.json').read_text())
        result.update(repetition=rep, variant=variant)
        report['runs'].append(result)
        (a.out / 'results.json').write_text(json.dumps(report, indent=2))
        print(f'{rep + 1}/{a.repeat} {variant}: all eight six-CPU cases passed', flush=True)
for mapping in ['block', 'page']:
    for span in ['2pages', '64pages', '2048pages', '65536pages']:
        samples = {variant: [r['seconds'] for suite in report['runs']
                            if suite['variant'] == variant for r in suite['runs']
                            if r['mapping'] == mapping and r['range'] == span]
                   for variant in ['baseline', 'candidate']}
        medians = {variant: statistics.median(values)
                   for variant, values in samples.items()}
        medians['baseline_over_candidate'] = medians['baseline'] / medians['candidate']
        medians['samples'] = samples
        report['medians'][f'{mapping}/{span}'] = medians
        print(f'{mapping}/{span}: {medians["baseline"]:.6f}s -> '
              f'{medians["candidate"]:.6f}s '
              f'({medians["baseline_over_candidate"]:.3f}x)', flush=True)
(a.out / 'results.json').write_text(json.dumps(report, indent=2))
