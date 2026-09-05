#!/usr/bin/env python3
"""Bounded, read-only process snapshot of an explicitly named owned trial.

Copies and records the supplied project postmortem reader before using it.
Pauses/resumes only the recorded trial; preserves its 1 GiB first-pass dump.
No guest register/memory writes or calls are performed. Empty first-pass
results do not establish absence from the unscanned RAM suffix.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from checkpoint_common import HMP, sha256


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('run',type=Path)
    p.add_argument('reader',type=Path)
    p.add_argument('--ram-base',type=lambda s:int(s,0),default=0x10000000000)
    p.add_argument('--process-pa',type=lambda s:int(s,0))
    p.add_argument('--cache-dir',type=Path)
    a=p.parse_args()
    if not 0x10000000000<=a.ram_base<0x10300000000 or a.ram_base%0x40000000:
        raise ValueError('expected a 1 GiB aligned chunk within this 12 GiB guest')
    launch=json.loads((a.run/'launch.json').read_text())
    endpoint=launch['argv'][launch['argv'].index('-monitor')+1]
    expected=f'unix:{a.run}/monitor.sock,server=on,wait=off'
    if endpoint!=expected:raise ValueError('monitor does not match the named owned trial')
    out=a.run/f'probe-snapshot-{time.time_ns()}';out.mkdir(exist_ok=False)
    shutil.copyfile(a.reader,out/'reader.py')
    spec=importlib.util.spec_from_file_location('snapshot_reader',out/'reader.py')
    reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
    h=HMP(a.run/'monitor.sock',timeout=30)
    if 'running' not in h.command('info status'):raise ValueError('expected running trial')
    started=time.monotonic()
    result=dict(reader_source=str(a.reader.resolve()),reader_sha256=sha256(out/'reader.py'),
        memory_writes=False,register_writes=False,scanned_bytes=0 if a.process_pa else 0x40000000,
        scanned_base=hex(a.ram_base),processes=[])
    try:
        h.command('stop')
        ram=out/'ram';ram.mkdir()
        if not a.process_pa:
            raw=ram/f'{a.ram_base:x}.bin'
            answer=h.command(f'pmemsave {a.ram_base:#x} 0x40000000 {json.dumps(str(raw))}')
            if not raw.exists() or raw.stat().st_size!=0x40000000:
                raise RuntimeError('RAM collection failed: '+answer)
        class Live(reader.Memory):
            def physical(self,address,size):
                try:return super().physical(address,size)
                except ValueError:
                    text=h.command(f'xp /{size}xb {address:#x}')
                    data=bytes(int(v,16) for v in re.findall(r'(?<![0-9a-fx])0x([0-9a-f]{2})(?![0-9a-f])',text))
                    if len(data)!=size:raise ValueError('short physical read')
                    return data
        memory=Live(a.run/'monitor.sock',ram)
        candidates=[(a.process_pa,memory.physical(a.process_pa,0x800))] if a.process_pa else memory.candidates('dvm-gpu-load')
        for pa,data in candidates:
            if data[0x55c:0x56c].split(b'\0')[0]!=b'dvm-gpu-load':
                raise ValueError('candidate no longer names the expected helper')
            try:result['processes'].append(reader.inspect(memory,'dvm-gpu-load',pa,data))
            except ValueError as error:result['processes'].append(dict(proc_pa=hex(pa),error=str(error)))
        if a.cache_dir and result['processes']:
            result['slide']=reader.resolve_slide(memory,result['processes'][0],a.cache_dir)
        print(json.dumps(result,indent=2))
    finally:
        h.command('cont')
        result['paused_seconds']=time.monotonic()-started
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
