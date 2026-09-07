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
p.add_argument('--scene',type=int,choices=range(5),default=0,help='0 moving, 1 alpha, 2 clip/transform, 3 image, 4 group opacity')
p.add_argument('--shared-surface',action='store_true',help='owned IOSurface CARenderer/display test; requires mapping-provider helper')
p.add_argument('--regions',action='store_true',help='append a public region-transfer probe after the single red CALayer control')
p.add_argument('--renderer-flags',type=lambda s:int(s,0),choices=(0,2),default=0,help='bounded exact-guest CARenderer coordinate-contract experiment')
p.add_argument('--orientation',action='store_true',help='asymmetric plain CALayer image CPU/GPU control')
p.add_argument('--uikit',action='store_true',help='actual UIKit view tree; offscreen or owned shared display target')
p.add_argument('--uikit-external-reference',action='store_true',help='retire after UIKit capture; pixel acceptance requires independent native-reference analysis')
p.add_argument('--uikit-animate',action='store_true',help='alternate UIKit card geometry and transparency in a displayed batch')
p.add_argument('--uikit-effect',choices=('none','blur','glass'),default='none',help='actual UIKit blur/glass view; offscreen experiment')
p.add_argument('--uikit-window',action='store_true',help='attach to a public UIWindow and record lifecycle state')
p.add_argument('--uikit-trace',action='store_true',help='observe original backing-store conversion in the isolated UIKit process')
a=p.parse_args()
if a.renderer_flags and not (a.orientation or a.uikit):p.error('renderer flags require an orientation or UIKit diagnostic')
if a.orientation and (a.uikit or a.frames!=1 or a.shared_surface or a.regions or a.scene):p.error('orientation requires --frames 1, offscreen, no other scene mode')
if a.uikit_window and (not a.uikit or a.shared_surface):p.error('window probe requires offscreen UIKit')
if a.uikit_effect!='none' and (not a.uikit or a.shared_surface):p.error('effect experiment requires offscreen UIKit')
if a.uikit_animate and not (a.uikit and a.shared_surface):p.error('UIKit animation requires displayed UIKit')
if a.uikit_trace and (not a.uikit or a.shared_surface):p.error('UIKit tracing requires the offscreen UIKit probe')
if a.uikit and a.shared_surface and a.renderer_flags!=2:p.error('displayed UIKit uses the verified renderer coordinate flags 2')
if a.uikit_external_reference and not a.uikit:p.error('external UIKit reference requires --uikit')
if a.uikit and (a.regions or a.scene or (not a.shared_surface and a.frames!=1)):p.error('UIKit requires one offscreen frame or a shared batch, no other scene mode')
if a.uikit and a.shared_surface and not a.uikit_external_reference:p.error('displayed UIKit requires external native pixel verification')
if a.regions and (a.frames!=1 or a.shared_surface):p.error('region probe requires --frames 1 without --shared-surface')
if a.frames!=1 and not 3<=a.frames<=4096:p.error('frames must be 1 or 3..4096')
if a.hz and a.frames==1:p.error('pacing requires a sequence')
if a.scene and a.frames==1:p.error('scene requires a sequence')
if a.shared_surface and not 3<=a.frames<=1024:p.error('shared display test requires 3..1024 frames')
root=Path(__file__).resolve().parents[2];src=root/'tools/gpu'
subprocess.run(['python3',str(src/'build_driver_revision.py'),str(a.base),str(a.out)],check=True)
sdk=subprocess.check_output(['xcrun','--sdk','iphoneos' if a.uikit else 'macosx','--show-sdk-path'],text=True).strip()
flags=['-target','arm64-apple-ios27.0','-isysroot',sdk,'-Wno-incompatible-sysroot',
       '-fobjc-arc','-fobjc-arc-exceptions','-O1','-Wall','-Wextra','-Werror',
       '-Wno-deprecated-declarations','-fno-objc-msgsend-selector-stubs']
uikit_headers=[f'-DDVM_CA_UIKIT_EFFECT={("none","blur","glass").index(a.uikit_effect)}']+(['-DDVM_ORIENTATION_GUEST'] if a.orientation else [])+(['-DDVM_CA_UIKIT'] if a.uikit else [])+(['-DDVM_CA_UIKIT_ANIMATE'] if a.uikit_animate else [])+(['-DDVM_CA_UIKIT_WINDOW'] if a.uikit_window else [])+(['-DDVM_CA_UIKIT_TRACE'] if a.uikit_trace else [])+(['-DDVM_CA_UIKIT_EXTERNAL_REFERENCE'] if a.uikit_external_reference else [])
subprocess.run(['xcrun','clang',*flags,*uikit_headers,f'-DDVM_CA_RENDERER_FLAGS={a.renderer_flags}',*(['-DDVM_CA_SHARED'] if a.shared_surface else []),*(['-DDVM_CA_REGIONS'] if a.regions else []),f'-DDVM_CA_FRAMES={a.frames}',f'-DDVM_CA_HZ={a.hz}',f'-DDVM_CA_SCENE={a.scene}','-c',str(src/'consumer_package.m'),'-o',str(a.out/'consumer_package.o')],check=True)
symbols=subprocess.check_output(['nm','-u',str(a.out/'consumer_package.o')],text=True).split()
stub=a.out/'stubs/usr/lib/libobjc.tbd';text=stub.read_text()
added=[s for s in symbols if (s.startswith('_objc_') or s in ('_class_getInstanceMethod','_method_setImplementation','_sel_registerName')) and '"'+s+'"' not in text]
stub.write_text(text.replace('symbols: [ ','symbols: [ '+''.join('"'+s+'", ' for s in added)))
stub=a.out/'stubs/usr/lib/libSystem.tbd';text=stub.read_text()
added=[s for s in ('_task_info','_mach_task_self_','_backtrace','_sigaction','_memset_pattern16') if '"'+s+'"' not in text]
stub.write_text(text.replace('symbols: [ ','symbols: [ '+''.join('"'+s+'", ' for s in added)))
for framework,names in {
    'Foundation':['_NSSetUncaughtExceptionHandler',*(['_OBJC_CLASS_$_NSThread','_OBJC_CLASS_$_NSRunLoop'] if a.uikit else [])],
    'CoreFoundation':['_CFDataCreate',*(['_CFGetTypeID','_CFCopyDescription'] if a.uikit else [])],
    'CoreGraphics':[*(['_CGContextSetInterpolationQuality'] if a.orientation else []),'_CGPointZero','_CGDataProviderCreateWithCFData','_CGDataProviderRelease','_CGImageCreate','_CGImageRelease',*(['_CGBitmapContextCreate','_CGBitmapContextCreateImage','_CGContextRelease','_CGContextTranslateCTM','_CGContextScaleCTM'] if a.uikit or a.orientation else [])],
    'QuartzCore':['_CATransform3DMakeScale','_kCAFilterNearest',*(['_CACurrentMediaTime'] if a.uikit or a.orientation else []),*(['_CABackingStoreGetTypeID'] if a.uikit_trace else [])],
}.items():
    stub=a.out/f'stubs/System/Library/Frameworks/{framework}.framework/{framework}.tbd'
    text=stub.read_text();added=[s for s in names if '"'+s+'"' not in text]
    stub.write_text(text.replace('symbols: [ ','symbols: [ '+''.join('"'+s+'", ' for s in added)))
binary=a.out/'DVMProxy.bundle/DVMProxy'
if a.uikit:
    stub=a.out/'stubs/System/Library/Frameworks/UIKit.framework/UIKit.tbd';stub.parent.mkdir(exist_ok=True)
    names=[s for s in symbols if s.startswith('_OBJC_CLASS_$_UI') or s in
           ('_UIAccessibilityIsReduceTransparencyEnabled','_UIAccessibilityIsReduceMotionEnabled')]
    stub.write_text('--- !tapi-tbd\ntbd-version: 4\ntargets: [ arm64-ios ]\ninstall-name: /System/Library/Frameworks/UIKit.framework/UIKit\nexports:\n  - targets: [ arm64-ios ]\n    symbols: [ '+', '.join('"'+s+'"' for s in names)+' ]\n...\n')
subprocess.run(['xcrun','clang',*flags,'-F',str(a.out/'stubs/System/Library/Frameworks'),'-L',str(a.out/'stubs/usr/lib'),
    '-dynamiclib','-Wl,-install_name,/usr/local/libexec/DVMProxy.bundle/DVMProxy',str(a.out/'driver_guest.o'),str(a.out/'consumer_package.o'),
    '-framework','Foundation','-framework','CoreFoundation','-framework','IOSurface','-framework','Metal',
    '-framework','QuartzCore','-framework','CoreGraphics',*(['-framework','UIKit'] if a.uikit else []),'-lobjc','-o',str(binary)],check=True)
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
record=dict(frames=a.frames,hz=a.hz,scene=a.scene,shared_surface=a.shared_surface,regions=a.regions,uikit=a.uikit,uikit_effect=a.uikit_effect,uikit_window=a.uikit_window,uikit_animate=a.uikit_animate,uikit_trace=a.uikit_trace,uikit_external_reference=a.uikit_external_reference,orientation=a.orientation,renderer_flags=a.renderer_flags,binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
            helper_sha256=hashlib.sha256((a.out/'dvm-gpu-load').read_bytes()).hexdigest(),
            scope='test export compiled; guest execution untested')
if record['helper_sha256']!=hashlib.sha256((a.base/'dvm-gpu-load').read_bytes()).hexdigest():
    raise ValueError('package unexpectedly changed the pinned helper')
for name in ('consumer_package.m','consumer_probe.inc','consumer_sequence_probe.inc','consumer_shared_probe.inc','consumer_shared_scene.inc','consumer_region_probe.inc','consumer_uikit_probe.inc','consumer_uikit_support.inc','consumer_uikit_effect_scene.inc','consumer_uikit_window.inc','consumer_uikit_display_scene.inc','consumer_uikit_scene.inc','test_layer_orientation.m'):
    (a.out/name).write_bytes((src/name).read_bytes())
(a.out/'consumer-package.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record))
