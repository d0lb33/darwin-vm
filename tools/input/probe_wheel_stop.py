#!/usr/bin/env python3
"""Capture a wheel gesture after release; inspect frames for residual motion.

A matching frame hash alone cannot prove a stop: a stalled display also stops
changing. Verify a reverse notch still scrolls afterward. This probe records
transport counters so concurrent touch/button input is not silently attributed
to the wheel under test.
"""
import argparse
import json
from pathlib import Path
import time
from types import SimpleNamespace

from native_input import HMP, norm, read_status, screendump, successful, wheel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--notches', type=int, default=-3)
    parser.add_argument('--x', type=int, default=589, help='framebuffer pixels')
    parser.add_argument('--y', type=int, default=1400, help='framebuffer pixels')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    hmp = HMP(args.run / 'monitor.sock')
    result = {'run': str(args.run), 'notches': args.notches,
              'before_sha256': screendump(hmp, str(args.out / 'before.png'))}
    hmp.sock.close()  # The QEMU HMP socket permits only one active client.
    result['dispatch'] = wheel(SimpleNamespace(ready_timeout=30, ack_timeout=40,
                                               frames=None), args.run,
                               norm(args.x, 1179, True), norm(args.y, 2556, True),
                               args.notches)
    completed_at = time.monotonic()
    hmp = HMP(args.run / 'monitor.sock')
    result['samples'] = []
    for delay in (.25, 1, 3):
        time.sleep(max(0, completed_at + delay - time.monotonic()))
        path = args.out / f'after-{delay:g}s.png'
        digest = screendump(hmp, str(path))
        result['samples'].append({'after_dispatch_seconds': time.monotonic() - completed_at,
                                  'frame': str(path), 'sha256': digest,
                                  'status': read_status(args.run / 'input-status.json')})
    result['dispatch_ok'] = successful(result['dispatch'])
    result['visual_verdict'] = 'requires frame inspection and a responsive reverse-notch control'
    (args.out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['dispatch_ok'] else 1)


if __name__ == '__main__':
    main()
