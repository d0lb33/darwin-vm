#!/usr/bin/env python3
"""Observe receipt/dispatch replies during a paced drag in an already-ready VM.

Host log-read timestamps are upper bounds on reply arrival, not guest event
execution timestamps. No VM lifecycle or driver changes are performed.
"""
import argparse
import json
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace
from native_input import QMP, HMP, ready, read_status, send, move, norm, outcome, idle


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--capture-dir',type=Path,required=True)
    p.add_argument('--x',type=int,default=900)
    p.add_argument('--from-y',type=int,required=True)
    p.add_argument('--to-y',type=int,required=True)
    p.add_argument('--duration-ms',type=int,default=400)
    p.add_argument('--steps',type=int,default=12)
    p.add_argument('--observe-seconds',type=float,default=5)
    a=p.parse_args()
    if not 2<=a.steps<=120 or not 50<=a.duration_ms<=2000 or not 1<=a.observe_seconds<=15:p.error('invalid bounded gesture')
    x=norm(a.x,1179,True);ys=[norm(round(a.from_y+(a.to_y-a.from_y)*i/(a.steps-1)),2556,True) for i in range(a.steps)]
    before=ready(SimpleNamespace(ready_timeout=5),a.run/'input-status.json')
    a.out.mkdir(exist_ok=False,parents=True)
    h=HMP(a.run/'monitor.sock');h.command(f'screendump {a.out}/before.png');h.sock.close()
    q=QMP(a.run/'qmp.sock');stop=threading.Event();started=threading.Event();records=[];poll_gaps=[]
    def observe():
        stderr = a.run / ('stderr.log' if (a.run/'stderr.log').exists()
                          else 'qemu.stderr.log')
        with (a.run/'serial.log').open('rb') as serial,stderr.open('rb') as err:
            files=[('serial',serial),('stderr',err)];buffers={name:b'' for name,_ in files}
            for _,f in files:f.seek(0,2)
            last=time.monotonic_ns();started.set()
            while not stop.is_set():
                now=time.monotonic_ns();poll_gaps.append(now-last);last=now
                for name,f in files:
                    buffers[name]+=f.read()
                    lines=buffers[name].split(b'\n');buffers[name]=lines.pop()
                    for line in lines:
                        s=line.decode(errors='replace')
                        if ('DVMI2' in s or 'darwin-input: timing' in s or 'transition file=' in s):
                            records.append(dict(observed_ns=time.monotonic_ns(),source=name,line=s))
                stop.wait(.002)
    t=threading.Thread(target=observe);t.start();started.wait(2)
    enable=a.capture_dir/'transition.enable';enable.touch()
    injection=[];t0=time.monotonic_ns()
    try:
        send(q,x,ys[0],True);injection.append(dict(kind='D',before_ns=t0,after_ns=time.monotonic_ns()))
        for i,y in enumerate(ys[1:],1):
            target=t0+round(a.duration_ms*1e6*i/(a.steps-1))
            time.sleep(max(0,(target-time.monotonic_ns())/1e9))
            b=time.monotonic_ns();move(q,x,y);injection.append(dict(kind='M',before_ns=b,after_ns=time.monotonic_ns()))
    finally:
        b=time.monotonic_ns();send(q,x,ys[-1],False);injection.append(dict(kind='U',before_ns=b,after_ns=time.monotonic_ns()))
        q.sock.close()
        stop.wait(a.observe_seconds)
        enable.unlink(missing_ok=True);stop.set();t.join(2)
    after=read_status(a.run/'input-status.json')
    h=HMP(a.run/'monitor.sock');h.command(f'screendump {a.out}/after.png');h.sock.close()
    report=dict(start_ns=t0,injection=injection,observations=records,before=before,after=after,
                counters=outcome(before,after),poll_gap_max_ms=max(poll_gaps)/1e6,
                released=after['epoch']==before['epoch'] and idle(after) and not after['contact_sent'],
                limitations='Reply times are host log observation upper bounds; guest-to-host report transport is included. Wire timestamps and scanout share QEMU clock. Visible response must be reviewed separately.')
    (a.out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('observations','before','after')},indent=2))
    if not report['released'] or any(report['counters'][k] for k in ['timeouts','dispatch_failed','ack_failed','ack_not_ready','overflow_or_not_ready_drops']):raise SystemExit('input failed: stop gesture reuse')


if __name__=='__main__':main()
