#!/usr/bin/env python3
"""Capture one complete paused 12 GiB owned driver failure; no guest writes."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from checkpoint_common import HMP, sha256

run=Path(sys.argv[1]);launch=json.loads((run/'launch.json').read_text())
argv=launch['argv']
if argv[argv.index('-monitor')+1] != f'unix:{run}/monitor.sock,server=on,wait=off' or argv[argv.index('-m')+1]!='12G':
    raise ValueError('not the named owned 12 GiB trial')
h=HMP(run/'monitor.sock',timeout=60)
if 'paused' not in h.command('info status'):raise ValueError('capture requires paused source')
out=run/'failure-snapshot';out.mkdir(exist_ok=False);ram=out/'ram';ram.mkdir()
report=dict(paused=True,bytes=0,memory_writes=False,register_writes=False,complete=False)
reader=Path(__file__).resolve().parents[1]/'re/warm_boot_postmortem.py'
shutil.copyfile(reader,out/'reader.py');report['reader_sha256']=sha256(out/'reader.py')
started=time.monotonic()
try:
    for base in range(0x10000000000,0x10300000000,0x40000000):
        path=ram/f'{base:x}.bin'
        answer=h.command(f'pmemsave {base:#x} 0x40000000 {json.dumps(str(path))}')
        if path.stat().st_size!=0x40000000:raise ValueError('short capture '+answer)
        report['bytes']+=path.stat().st_size
        print(f'captured {report["bytes"]//(1024**3)}/12 GiB',flush=True)
    report['complete']=True
    spec=importlib.util.spec_from_file_location('driver_postmortem',out/'reader.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    memory=module.Memory(run/'monitor.sock',ram)
    report['processes']=[]
    for name in ('dvm-gpu-load','dvm-input','launchd'):
        for pa,data in memory.candidates(name):
            try:entry=module.inspect(memory,name,pa,data)
            except Exception as error:entry=dict(name=name,proc_pa=hex(pa),error=str(error))
            if entry.get('threads'):
                for thread in entry['threads']:
                    stack = int(thread.get('kernel_stack','0'),16)
                    if stack:
                        try:
                            data=memory.kernel(stack,0x4000)
                            stack_name=f'kernel-stack-{entry["pid"]}-{thread["thread"]}.bin'
                            (out/stack_name).write_bytes(data)
                            thread['kernel_stack_file']=stack_name
                        except Exception as error:thread['kernel_stack_error']=str(error)
            report['processes'].append(entry)
    report['kernel_pages']={hex(va):hex(pa) for va,pa in memory.pages.items()}
    print(json.dumps(report['processes'],indent=2),flush=True)
except Exception as error:
    report['error']=f'{type(error).__name__}: {error}'
    raise
finally:
    report['elapsed']=time.monotonic()-started
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
