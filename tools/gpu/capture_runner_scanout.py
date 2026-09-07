#!/usr/bin/env python3
"""Collect QEMU's one-shot final DCP witness between jobs, then resume the VM.

The current QEMU witness exports once at its first stop. This is a bounded
between-batch diagnostic, not a checkpoint or a repeatable presentation API.
Run only as the sole owner of an idle development runner.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from checkpoint_common import HMP
from verify_runner_job import verify
from shared_consumer_verify import verify_scanout_export


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trial',type=Path)
    parser.add_argument('--job',type=int,required=True)
    args=parser.parse_args();trial=args.trial.resolve()
    pid=int((trial/'qemu.pid').read_text())
    command=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True)
    if 'qemu-system-aarch64' not in command or not any(str(p/'disk.qcow2') in command for p in (trial,args.trial)):
        raise ValueError('owned QEMU is not live')
    if (trial/'last-presented.bgra').exists():raise ValueError('one-shot witness already exported')
    if any((trial/'runner-inbox').iterdir()):raise ValueError('runner has queued work')
    jobs=sorted((trial/'runner-jobs').iterdir())
    for job in jobs:
        if not (job/'result.json').exists():raise ValueError('runner job incomplete')
        result=json.loads((job/'result.json').read_text())
        if result.get('shared_surface') and not result.get('verified'):
            raise ValueError('failed shared ownership requires recovery')
    shared=[j for j in jobs if json.loads((j/'job.json').read_text()).get('shared_surface')]
    if not shared or shared[-1].name!=str(args.job):raise ValueError('job is not the latest displayed batch')
    verify(shared[-1])
    monitor=HMP(trial/'monitor.sock',timeout=5)
    if 'running' not in monitor.command('info status'):raise ValueError('VM was not running')
    record=dict(job=args.job,pid=pid,verified=False,resumed=False)
    start=time.monotonic()
    try:
        monitor.command('stop')
        record.update(verify_scanout_export(trial,shared[-1]))
    finally:
        monitor.command('cont')
        record['resumed']='running' in monitor.command('info status')
        record['pause_and_collection_seconds']=time.monotonic()-start
        (trial/f'scanout-{args.job}.json').write_text(json.dumps(record,indent=2)+'\n')
    if not record['resumed']:raise RuntimeError('owned VM did not resume')
    print(json.dumps(record))


if __name__=='__main__':main()
