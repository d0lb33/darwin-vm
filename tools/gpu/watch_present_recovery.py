#!/usr/bin/env python3
"""Read-only post-batch witness; do not mistake a delayed counter for new scanout."""
import argparse
import json
from pathlib import Path
import time

p=argparse.ArgumentParser(description=__doc__);p.add_argument('trial',type=Path);a=p.parse_args()
deadline=time.monotonic()+300
while not (a.trial/'present-recovery-baseline.json').exists():
    if time.monotonic()>deadline or (a.trial/'result.json').exists():raise RuntimeError('batch did not reach recovery gate')
    time.sleep(.05)
log=a.trial/'stderr.log';offset=log.stat().st_size;started=time.monotonic_ns()
deadline=time.monotonic()+30
with log.open('rb') as f:
    f.seek(offset);data=b''
    while time.monotonic()<deadline:
        data+=f.read()
        first=data.find(b'iomfb: presented ')
        if first>=0 and b'D594 nested completed, status 0x0' in data[first:]:
            report=dict(verified=True,after_batch=True,log_offset=offset,host_started_ns=started,
                host_observed_ns=time.monotonic_ns(),scope='new normal scanout and native completion appended after the batch recovery gate',
                evidence=data[first:].decode(errors='replace'))
            (a.trial/'post-batch-native.json').write_text(json.dumps(report,indent=2)+'\n')
            print('New native scanout and D594 observed after batch completion.');break
        time.sleep(.05)
    else:raise RuntimeError('no post-batch native scanout/completion')
