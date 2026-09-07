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
    p.add_argument('--frames',type=int,default=1,help='required frame count for package-scene verification')
    p.add_argument('--scene',type=int,choices=range(5),default=0,help='0 moving, 1 alpha, 2 clip/transform, 3 image, 4 group opacity')
    p.add_argument('--shared-surface',action='store_true',help='require shared CARenderer/surface/native scanout evidence')
    p.add_argument('--uikit-reference',type=Path,help='native full-screen reference directory; explicit UIKit pixel acceptance')
    p.add_argument('--surface-handoff',action='store_true',help='also require supervisor/child Mach-port alias witnesses')
    p.add_argument('--hz',type=int,choices=(0,30,60),default=0,help='required shared-surface pacing rate; 0 means unpaced')
    p.add_argument('--mode',choices=('data','installed','system'),default='data',help='staging location; system mode remounts only the disposable guest root writable')
    p.add_argument('--development',action='store_true',help='explicit per-worker development open; requires matching opt-in kernel and helper entitlement')
    a=p.parse_args()
    if (a.frames!=1 and not 3<=a.frames<=4096) or (a.test=='builtin' and a.frames!=1):p.error('invalid test frame count')
    if a.scene and a.frames==1:p.error('scene requires sequence frames')
    if a.shared_surface and (a.test!='package' or not 3<=a.frames<=1024):p.error('shared surface requires package, 3..1024 frames')
    if a.hz and not a.shared_surface:p.error('this pacing verifier requires shared surface')
    if a.surface_handoff and not a.shared_surface:p.error('surface handoff requires shared surface')
    trial=a.trial.resolve();pid=int((trial/'qemu.pid').read_text())
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
    result=dict(job=job,bundle=str(bundle),sha256=digest,signature=signature,expected=a.expected,test=a.test,frames=a.frames,scene=a.scene,shared_surface=a.shared_surface,surface_handoff=a.surface_handoff,hz=a.hz,mode=a.mode,development=a.development,
                queued_monotonic=time.monotonic(),scope='host signature validation; guest loading is untested until job completes')
    if a.uikit_reference:
        if not a.shared_surface or a.scene:p.error('UIKit reference requires shared surface, no numbered CALayer scene')
        folder=a.uikit_reference.resolve();meta=json.loads((folder/'manifest.json').read_text())
        if meta.get('display_frame')!=a.frames or meta.get('forwarded') or meta.get('exit')!=0:p.error('native reference frame/mode mismatch')
        result['uikit_reference']=dict(directory=str(folder),**{key:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name,key in (('gpu.bgra','gpu_sha256'),('manifest.json','manifest_sha256'),('result.log','log_sha256'))})
    if a.worker:result['worker']=str(a.worker.resolve())
    temp=inbox/f'{job:020d}.tmp';temp.write_text(json.dumps(result,indent=2)+'\n')
    os.rename(temp,inbox/f'{job:020d}.json');print(json.dumps(dict(job=job,sha256=digest)))


if __name__=='__main__':main()
