#!/usr/bin/env python3
"""Sample one owned host worker's RSS during a bounded guest runner job."""
import argparse
import json
from pathlib import Path
import subprocess
import time

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('trial',type=Path);p.add_argument('job',type=int)
a=p.parse_args();directory=a.trial/'runner-jobs'/str(a.job)
deadline=time.monotonic()+60;metadata=directory/'worker.json'
while not metadata.exists() and time.monotonic()<deadline:time.sleep(.1)
if not metadata.exists():raise TimeoutError('worker metadata deadline')
worker=json.loads(metadata.read_text());samples=[]
started_after_completion=(directory/'result.json').exists()
while time.monotonic()<deadline:
    r=subprocess.run(['ps','-p',str(worker['pid']),'-o','pid=,rss=,command='],capture_output=True,text=True)
    if r.returncode:break
    fields=r.stdout.strip().split(maxsplit=2)
    if len(fields)!=3 or int(fields[0])!=worker['pid'] or fields[2]!=worker['path']:
        raise ValueError('worker PID ownership changed')
    samples.append(dict(monotonic=time.monotonic(),rss_kib=int(fields[1])))
    if (directory/'result.json').exists():break
    time.sleep(.2)
result=dict(scope='sampled-host-worker-RSS-not-guest-or-total-GPU-memory',worker=worker,
    samples=samples,peak_sampled_rss_kib=max((x['rss_kib'] for x in samples),default=None),
    started_after_completion=started_after_completion,completed=(directory/'result.json').exists())
with (directory/'host-memory.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('samples','worker')}))
if not samples or not result['completed'] or started_after_completion:raise SystemExit(1)
