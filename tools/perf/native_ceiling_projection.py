#!/usr/bin/env python3
"""Project whole-system HVF cost from bridge micro-costs and measured rates.

Inputs are native_bridge_bench.py result directories (one per bridge variant)
and the compatibility-operation rates measured under TCG in
docs/re/hvf-migration-call-profile.md (HVF_MIG_CALLS2, a 60 s window of the
first-boot Data migration on one CPU).

For each variant the per-operation cost is the median loop time divided by
its iteration count, minus the zero-iteration measurement floor. The
projection charges every counted Apple register operation, guarded pair and
MMIO access at those costs and reports host seconds per guest second of TCG
work. It is a sensitivity model, not a boot measurement: ordinary ARM system
register traps, interrupts, page faults and syscalls are not counted, so the
projection is optimistic for the bridge.
"""
import argparse
import json
from pathlib import Path

# HVF_MIG_CALLS2: 60.023794 s window, one TCG vCPU.
WINDOW = 60.023794
RATES = {
    'apple_reads': 23155648 / WINDOW,
    'apple_writes': 3494349 / WINDOW,
    'pairs': 744399 / WINDOW,
    'mmio': (145966 + 329480) / WINDOW,
}
HOT_SHARE = 0.6679   # TPIDR_GL2 + CURRENTG share of Apple register traffic
# Native-vs-TCG compute ratios measured in hvf-performance-checkpoint.md.
COMPUTE = {'integer': 1.02, 'pointer-chase': 1.45, 'load/store': 2.79}


def cost(report, label):
    med = report['medians']
    floor = med['measurement_floor']['hvf']
    return (med[label]['hvf'] - floor) / med[label]['iterations']


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('variants', nargs='+', help='NAME=results_dir')
    ap.add_argument('--json', type=Path)
    a = ap.parse_args()
    out = {'rates_per_second': RATES, 'variants': {}}
    print(f"{'variant':10s} {'trap read':>10s} {'GL2 read':>10s} {'native rd':>10s} "
          f"{'pair':>10s} | {'overhead':>9s} {'int':>6s} {'ptr':>6s} {'ld/st':>6s}")
    for spec in a.variants:
        name, _, path = spec.partition('=')
        r = json.loads((Path(path) / 'results.json').read_text())
        trap = cost(r, 'tpidr_read')
        gl2 = cost(r, 'tpidr_gl2_read')
        native = cost(r, 'native_read')
        pair = cost(r, 'genter_gexit')
        regs = RATES['apple_reads'] + RATES['apple_writes']
        hot = regs * HOT_SHARE
        rest = regs - hot
        # Two projections: hot reads still trapping at the GL2-read cost, and
        # hot reads rewritten to non-trapping storage (native cost).
        rows = {}
        for mode, hot_cost in (('trapping', gl2), ('rewritten', native)):
            overhead = (hot * hot_cost + rest * trap +
                        RATES['pairs'] * pair + RATES['mmio'] * trap)
            rows[mode] = {'overhead': overhead,
                          'speed': {k: v / (1 + overhead) for k, v in COMPUTE.items()}}
        out['variants'][name] = {'trap_read_s': trap, 'gl2_read_s': gl2,
                                 'native_read_s': native, 'pair_s': pair,
                                 'projection': rows,
                                 'bridge_env': r.get('bridge_env', [])}
        for mode, row in rows.items():
            s = row['speed']
            print(f"{name + '/' + mode[:4]:10s} {trap*1e6:9.2f}us {gl2*1e6:9.2f}us "
                  f"{native*1e9:9.2f}ns {pair*1e6:9.2f}us | {row['overhead']:8.2f}x "
                  f"{s['integer']:6.2f} {s['pointer-chase']:6.2f} {s['load/store']:6.2f}")
    print("\noverhead = extra host seconds per guest second of TCG-equivalent work;")
    print("speed columns = projected HVF speed relative to TCG (>1 is faster) for")
    print("each measured compute ratio, ignoring uncounted traps (optimistic).")
    if a.json:
        a.json.write_text(json.dumps(out, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
