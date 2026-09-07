#!/usr/bin/env python3
"""Incremental guest driver plus CARenderer test export; pinned helper unchanged."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('base',type=Path);p.add_argument('out',type=Path)
p.add_argument('--frames',type=int,default=4)
p.add_argument('--hz',type=int,choices=(0,30,60),default=0)
p.add_argument('--scene',type=int,choices=range(4),default=0,help='0 moving, 1 alpha, 2 clip/transform, 3 image')
p.add_argument('--shared-surface',action='store_true',help='owned IOSurface CARenderer/display test; requires mapping-provider helper')
a=p.parse_args()
if a.frames!=1 and not 3<=a.frames<=4096:p.error('frames must be 1 or 3..4096')
if a.hz and a.frames==1:p.error('pacing requires a sequence')
if a.scene and a.frames==1:p.error('scene requires a sequence')
if a.shared_surface and (a.scene or a.hz or not 3<=a.frames<=16):p.error('initial shared display test requires 3..16 frames, scene 0 and no pacing')
root=Path(__file__).resolve().parents[2];src=root/'tools/gpu'
subprocess.run(['python3',str(src/'build_driver_revision.py'),str(a.base),str(a.out)],check=True)
sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
flags=['-target','arm64-apple-ios27.0','-isysroot',sdk,'-Wno-incompatible-sysroot',
       '-fobjc-arc','-fobjc-arc-exceptions','-O1','-Wall','-Wextra','-Werror',
       '-Wno-deprecated-declarations','-fno-objc-msgsend-selector-stubs']
subprocess.run(['xcrun','clang',*flags,*(['-DDVM_CA_SHARED'] if a.shared_surface else []),f'-DDVM_CA_FRAMES={a.frames}',f'-DDVM_CA_HZ={a.hz}',f'-DDVM_CA_SCENE={a.scene}','-c',str(src/'consumer_package.m'),'-o',str(a.out/'consumer_package.o')],check=True)
symbols=subprocess.check_output(['nm','-u',str(a.out/'consumer_package.o')],text=True).split()
stub=a.out/'stubs/usr/lib/libobjc.tbd';text=stub.read_text()
added=[s for s in symbols if s.startswith('_objc_') and '"'+s+'"' not in text]
stub.write_text(text.replace('symbols: [ ','symbols: [ '+''.join('"'+s+'", ' for s in added)))
stub=a.out/'stubs/usr/lib/libSystem.tbd';text=stub.read_text()
added=[s for s in ('_task_info','_mach_task_self_','_backtrace','_sigaction') if '"'+s+'"' not in text]
stub.write_text(text.replace('symbols: [ ','symbols: [ '+''.join('"'+s+'", ' for s in added)))
for framework,names in {
    'Foundation':['_NSSetUncaughtExceptionHandler'],
    'CoreFoundation':['_CFDataCreate'],
    'CoreGraphics':['_CGPointZero','_CGDataProviderCreateWithCFData','_CGDataProviderRelease','_CGImageCreate','_CGImageRelease'],
    'QuartzCore':['_CATransform3DMakeScale','_kCAFilterNearest'],
}.items():
    stub=a.out/f'stubs/System/Library/Frameworks/{framework}.framework/{framework}.tbd'
    text=stub.read_text();added=[s for s in names if '"'+s+'"' not in text]
    stub.write_text(text.replace('symbols: [ ','symbols: [ '+''.join('"'+s+'", ' for s in added)))
binary=a.out/'DVMProxy.bundle/DVMProxy'
subprocess.run(['xcrun','clang',*flags,'-F',str(a.out/'stubs/System/Library/Frameworks'),'-L',str(a.out/'stubs/usr/lib'),
    '-dynamiclib','-Wl,-install_name,/usr/local/libexec/DVMProxy.bundle/DVMProxy',str(a.out/'driver_guest.o'),str(a.out/'consumer_package.o'),
    '-framework','Foundation','-framework','CoreFoundation','-framework','IOSurface','-framework','Metal',
    '-framework','QuartzCore','-framework','CoreGraphics','-lobjc','-o',str(binary)],check=True)
subprocess.run(['codesign','--force','--sign','-','--timestamp=none',str(binary.parent)],check=True)
subprocess.run(['codesign','--verify','--strict',str(binary.parent)],check=True)
signature=subprocess.run(['codesign','-d','-vvv',str(binary.parent)],capture_output=True,text=True,check=True).stderr
(a.out/'DVMProxy.codesign.txt').write_text(signature)
hashes=[]
for name in ('DVMProxy','dvm-gpu-load'):
    hashes.extend(re.findall(r'^CDHash=(\w+)$',(a.out/(name+'.codesign.txt')).read_text(),re.M))
if len(hashes)!=2:raise ValueError('signing identities')
(a.out/'hashes.txt').write_text('\n'.join(hashes)+'\n')
subprocess.run(['python3',str(root/'build_tc.py'),str(a.out/'hashes.txt'),str(a.out/'helper.tc')],check=True)
revision=json.loads((a.out/'revision.json').read_text())
revision['driver_cdhash']=hashes[0]
revision['consumer_package_relinked']=True
(a.out/'revision.json').write_text(json.dumps(revision,indent=2)+'\n')
imports=a.out/'consumer-package.nm-u';imports.write_bytes(subprocess.check_output(['nm','-u',str(binary)]))
(a.out/'DVMProxy.nm-u').write_bytes(imports.read_bytes())
subprocess.run(['python3',str(src/'verify_guest_imports.py'),'--output',str(a.out/'consumer-package-imports.tsv'),str(imports)],check=True)
record=dict(frames=a.frames,hz=a.hz,scene=a.scene,shared_surface=a.shared_surface,binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
            helper_sha256=hashlib.sha256((a.out/'dvm-gpu-load').read_bytes()).hexdigest(),
            scope='test export compiled; guest execution untested')
if record['helper_sha256']!=hashlib.sha256((a.base/'dvm-gpu-load').read_bytes()).hexdigest():
    raise ValueError('package unexpectedly changed the pinned helper')
for name in ('consumer_package.m','consumer_probe.inc','consumer_sequence_probe.inc','consumer_shared_probe.inc'):
    (a.out/name).write_bytes((src/name).read_bytes())
(a.out/'consumer-package.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record))
