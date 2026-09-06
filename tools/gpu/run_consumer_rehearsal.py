#!/usr/bin/env python3
"""Host QuartzCore selector rehearsal. Explicitly NOT exact-guest evidence."""
import argparse,json,os,subprocess
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('build',type=Path);p.add_argument('air',type=Path);p.add_argument('out',type=Path)
a=p.parse_args();a.out.mkdir(exist_ok=False)
fr,fw=os.pipe();br,bw=os.pipe()
with (a.out/'worker.log').open('wb') as wlog,(a.out/'client.log').open('wb') as clog:
    worker=subprocess.Popen([str(a.build/'driver_host')],stdin=fr,stdout=bw,stderr=wlog,env={**os.environ,'DVM_DRIVER_LIBRARY':str(a.air)})
    client=subprocess.Popen([str(a.build/'driver_client'),'consumer',str(a.air),'0'],stdin=br,stdout=fw,stderr=clog,env={**os.environ,'DVM_REHEARSAL_AIR':str(a.air)})
    for fd in (fr,fw,br,bw):os.close(fd)
    try:status=client.wait(timeout=20);worker.wait(timeout=5)
    finally:
        for proc in (client,worker):
            if proc.poll() is None:proc.kill();proc.wait()
(a.out/'result.json').write_text(json.dumps(dict(scope='host QuartzCore only',client_exit=status,worker_exit=worker.returncode))+'\n')
print((a.out/'client.log').read_text())
