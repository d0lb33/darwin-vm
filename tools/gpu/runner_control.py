#!/usr/bin/env python3
"""Queue a signed driver package in an already running isolated GPU test VM."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('trial',type=Path)
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--bundle',type=Path)
    group.add_argument('--stop',action='store_true')
    p.add_argument('--expected',choices=('pass','observe'),default='pass')
    p.add_argument('--worker',type=Path,help='matching host backend revision; default is the boot runner backend')
    p.add_argument('--test',choices=('builtin','package'),default='builtin',help='built-in red control or explicit DVMRunGuestTest export')
    p.add_argument('--mode',choices=('data','installed','system'),default='data',help='staging location; system mode remounts only the disposable guest root writable')
    p.add_argument('--development',action='store_true',help='explicit per-worker development open; requires matching opt-in kernel and helper entitlement')
    a=p.parse_args();trial=a.trial.resolve();pid=int((trial/'qemu.pid').read_text())
    command=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True).strip()
    # Refuse PID reuse and unrelated machines. QEMU argv may spell /tmp rather
    # than its /private/tmp canonical path on macOS.
    if 'qemu-system-aarch64' not in command or not any(str(path/'disk.qcow2') in command for path in (trial,a.trial)):
        p.error('owned QEMU process is not live')
    inbox=trial/'runner-inbox'
    if not inbox.is_dir():p.error('not a persistent runner')
    if a.stop:
        (inbox/'stop').touch(exist_ok=False);print('queued stop after pending jobs');return
    bundle=a.bundle.resolve()
    subprocess.run(['codesign','--verify','--strict',str(bundle)],check=True)
    signature=subprocess.run(['codesign','-d','-vvv',str(bundle)],capture_output=True,text=True,check=True).stderr
    digest=hashlib.sha256((bundle/'DVMProxy').read_bytes()).hexdigest()
    job=int(time.time_ns()//1000)
    result=dict(job=job,bundle=str(bundle),sha256=digest,signature=signature,expected=a.expected,test=a.test,mode=a.mode,development=a.development,
                queued_monotonic=time.monotonic(),scope='host signature validation; guest loading is untested until job completes')
    if a.worker:result['worker']=str(a.worker.resolve())
    temp=inbox/f'{job:020d}.tmp';temp.write_text(json.dumps(result,indent=2)+'\n')
    os.rename(temp,inbox/f'{job:020d}.json');print(json.dumps(dict(job=job,sha256=digest)))


if __name__=='__main__':main()
