#!/usr/bin/env python3
"""Observe native scanout/completion and fresh input ACKs after an owned job."""
import argparse
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from driver_peer import DriverPeer
from present_peer import observe_native_recovery

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('trial',type=Path);p.add_argument('--after-job',type=int,required=True)
p.add_argument('--label',default='',help='distinct observation attempt label')
a=p.parse_args();trial=a.trial.resolve()
if a.label and (not a.label.isalnum() or len(a.label)>24):p.error('invalid observation label')
pid=int((trial/'qemu.pid').read_text())
command=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True)
if 'qemu-system-aarch64' not in command or not any(str(x/'disk.qcow2') in command for x in (trial,a.trial)):
    raise ValueError('owned QEMU is not live')
until=time.monotonic()+35;job=trial/'runner-jobs'/str(a.after_job)/'result.json'
while not job.exists() and time.monotonic()<until:time.sleep(.2)
if not job.exists() or not json.loads(job.read_text()).get('verified'):raise ValueError('required job not verified')
before=json.loads((trial/'input-status.json').read_text())
offset=(trial/'stderr.log').stat().st_size;started=time.monotonic_ns()
observer=SimpleNamespace(out=trial,started=time.monotonic(),ready_since=None,ready_identity=None,ready_acks=0)
result=dict(job=a.after_job,pid=pid,before=before,log_offset=offset,verified=False)
while time.monotonic()<until:
    after=DriverPeer.observe_display(observer)
    witness=observe_native_recovery(trial,offset,started)
    if after and witness and after['presents']>before['presents'] and after['input_status']['acked']>before['acked']:
        result.update(verified=True,after=after,native_completion=witness);break
    time.sleep(.2)
path=trial/f'runner-recovery-{a.after_job}{"-"+a.label if a.label else ""}.json'
with path.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('before','after','native_completion')}))
if not result['verified']:raise SystemExit(1)
