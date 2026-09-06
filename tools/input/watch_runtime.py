#!/usr/bin/env python3
"""Freeze an owned input test at a rejected device request, before timeout.

Read existing and new lines; write the matched evidence before HMP stop.
The caller chooses an explicit test directory, never an inferred VM PID.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    pattern = re.compile(r'sks .*rejected unsupported|panic\(cpu|DVM_INPUT_ERROR')
    files = [open(args.run / name, errors='replace')
             for name in ('stderr.log', 'serial.log')]
    while True:
        for stream in files:
            for line in stream.readlines():
                if pattern.search(line):
                    evidence = dict(time=time.time(), file=stream.name,
                                    line=line.rstrip())
                    (args.run / 'runtime-stop.json').write_text(
                        json.dumps(evidence, indent=2) + '\n')
                    subprocess.run(['python3', str(Path(__file__).parents[1] / 'hmp.py'),
                                    str(args.run / 'monitor.sock'), 'stop'], check=True)
                    print(json.dumps(evidence), flush=True)
                    return
        time.sleep(.1)


if __name__ == '__main__':
    main()
