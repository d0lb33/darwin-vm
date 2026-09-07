#!/usr/bin/env python3
"""One bounded Home observation in an existing owned development session."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from session_cli import read_json
from report_compositor_pacing import analyze


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=4)
    a = p.parse_args()
    if not 2 <= a.seconds <= 10:
        p.error('observation must be 2..10 seconds')
    root = Path(__file__).resolve().parents[1]
    run = a.session/'run'
    before = read_json(a.session/'status.json', {})
    if not before.get('alive') or not before.get('ready') or not before.get('reuse'):
        raise ValueError('session is not healthy, ready and reusable')
    a.out.mkdir(exist_ok=False)
    def command(*args):
        r = subprocess.run([sys.executable, str(root/'input/native_input.py'), '--run', str(run), *args],
                           capture_output=True, text=True, timeout=45)
        if r.returncode:
            raise RuntimeError(r.stdout+r.stderr)
        return json.loads(r.stdout)
    command('frame', str(a.out/'home-before.ppm'))
    # native_input's frame command emits PNG regardless of filename. Decode
    # by content in the reporter, as Pillow does for ordinary screendumps.
    offset = (run/'stderr.log').stat().st_size
    started = time.monotonic_ns()
    (run/'transition.enable').touch()
    try:
        result = command('home')
        while time.monotonic_ns()-started < a.seconds*1e9:
            status = read_json(a.session/'status.json', {})
            if not status.get('alive') or not status.get('reuse') or status.get('generation') != before['generation']:
                raise RuntimeError('session failed or owner changed during trial')
            time.sleep(.05)
    finally:
        (run/'transition.enable').unlink(missing_ok=True)
    ended = time.monotonic_ns()
    command('frame', str(a.out/'home-after.png'))
    with (run/'stderr.log').open('rb') as f:
        f.seek(offset); raw = f.read().decode(errors='replace')
    (a.out/'raw-stderr.log').write_text(raw)
    # Keep a completed prefix for strict pairing, retaining the raw suffix.
    ends = list(re.finditer(r'^iomfb: swap id .*D594 .*completed, status .*\n', raw, re.M))
    if not ends:
        raise ValueError('no completed presentation after input')
    text = raw[:ends[-1].end()]
    (a.out/'stderr.log').write_text(text)
    for name in re.findall(r'transition file=(transition-\d{4}.ppm)', text):
        shutil.copyfile(run/name, a.out/name)
    (a.out/'presentation-pacing.json').write_text(json.dumps(analyze(text),indent=2)+'\n')
    journal = (run/'driver-host.jsonl').read_text()
    complete = journal[:journal.rfind('\n')+1]
    rows = [json.loads(line) for line in complete.splitlines() if line]
    rows = [r for r in rows if started <= r['host_received_ns'] <= ended]
    (a.out/'driver-host.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    after = read_json(a.session/'status.json', {})
    receipt = dict(scope=__doc__, started_ns=started, ended_ns=ended, input=result,
                   before=before, after=after, rpc_count=len(rows))
    (a.out/'trial.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(dict(input=result,requests=len(rows),generation=after.get('generation')),indent=2))


if __name__ == '__main__':
    main()
