#!/usr/bin/env python3
"""Replay captured guest JSON submissions against a fresh native Metal worker."""
import argparse
import hashlib
import json
import os
import select
from pathlib import Path
import struct
import subprocess
import time


def read_exact(stream,n):
    data=bytearray();until=time.monotonic()+15
    while len(data)<n:
        left=until-time.monotonic()
        if left<=0 or not select.select([stream],[],[],left)[0]:raise TimeoutError('worker reply deadline')
        part=os.read(stream.fileno(),n-len(data))
        if not part:raise EOFError('worker reply')
        data.extend(part)
    return bytes(data)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('trial',type=Path);p.add_argument('--worker',type=Path,required=True)
    p.add_argument('--library',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();a.out.mkdir(exist_ok=False)
    rows=[json.loads(x) for x in (a.trial/'driver-host.jsonl').read_text().splitlines()]
    env={k:v for k,v in os.environ.items() if not k.startswith('DVM_DRIVER_')}
    env['DVM_DRIVER_LIBRARY']=str(a.library.resolve())
    started=time.monotonic();passed=False;count=0
    # The outer command can enforce an observation deadline. This worker never
    # touches a VM, and its process identity is recorded before the first call.
    with (a.out/'worker.log').open('wb') as log:
        worker=subprocess.Popen([str(a.worker.resolve())],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,env=env)
        (a.out/'process.json').write_text(json.dumps(dict(pid=worker.pid,worker=str(a.worker.resolve()),sha256=hashlib.sha256(a.worker.read_bytes()).hexdigest()))+'\n')
        try:
            for row in rows:
                if row['op'].startswith('runner'):continue
                if row.get('wire_encoding','json')!='json':raise ValueError('only captured JSON submissions supported')
                request=row['request']
                if 'upload_file' in row:
                    payload=(a.trial/row['upload_file']).read_bytes()
                    if hashlib.sha256(payload).hexdigest()!=row['upload_sha256']:raise ValueError('upload capture hash')
                    request=json.loads(payload)
                    if {k:v for k,v in request.items() if k!='data'}!=row['request']:raise ValueError('upload descriptor mismatch')
                if row['op'] in ('upload','writeRenderBuffer') and 'data' not in request:raise ValueError('missing generated upload bytes')
                raw=json.dumps(request).encode();worker.stdin.write(struct.pack('<I',len(raw))+raw);worker.stdin.flush()
                n,=struct.unpack('<I',read_exact(worker.stdout,4))
                if not 0<n<=2*1024*1024:raise ValueError('reply extent')
                actual=json.loads(read_exact(worker.stdout,n));expected=row['reply']
                # Clock-domain samples vary between runs; outputs, statuses,
                # resource counts and error contracts must still agree.
                ignore={'gpu_start','gpu_end','kernel_start','kernel_end','gpu_us'}
                matched={k:v for k,v in actual.items() if k not in ignore}=={k:v for k,v in expected.items() if k not in ignore}
                with (a.out/'replies.jsonl').open('a') as f:f.write(json.dumps(dict(seq=request['seq'],op=row['op'],matched=matched,reply=actual))+'\n')
                if not matched:raise ValueError(f'reply contract differs at seq {request["seq"]} ({row["op"]})')
                count+=1
            passed=True
        finally:
            worker.stdin.close()
            try:worker.wait(timeout=5)
            except subprocess.TimeoutExpired:worker.kill();worker.wait()
            worker.stdout.close()
            passed=passed and worker.returncode==0
            result=dict(scope='captured-guest-submission-host-replay-not-new-guest-execution',passed=passed,
                        requests=count,worker_exit=worker.returncode,seconds=time.monotonic()-started)
            (a.out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
            if worker.returncode:raise RuntimeError('replay worker did not exit cleanly')


if __name__=='__main__':main()
