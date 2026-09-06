#!/usr/bin/env python3
"""Pause an owned live VM on a log condition, preserving its warm state."""
import argparse
import json
import re
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--monitor', required=True)
    parser.add_argument('--log', required=True)
    parser.add_argument('--pattern', default='rejected unsupported|panic\\(cpu')
    parser.add_argument('--report', required=True)
    parser.add_argument('--seconds', type=float, default=600)
    args = parser.parse_args()
    pattern = re.compile(args.pattern)
    started = time.monotonic()
    with open(args.log, errors='replace') as source:
        while time.monotonic() - started < args.seconds:
            line = source.readline()
            if not line:
                time.sleep(.2)
                continue
            if not pattern.search(line):
                continue
            command = ['python3', str(Path(__file__).parents[1] / 'hmp.py'),
                       args.monitor, 'stop']
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=10)
            report = {'line': line.strip(), 'elapsed': time.monotonic()-started,
                      'returncode': result.returncode, 'output': result.stdout,
                      'error': result.stderr}
            Path(args.report).write_text(json.dumps(report, indent=2))
            print(json.dumps(report), flush=True)
            return result.returncode
    print('Guard deadline reached; VM left unchanged', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
