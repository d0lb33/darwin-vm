#!/usr/bin/env python3
"""Bounded Mac Catalyst UIKit rendering control; never boots or accesses a VM."""
import argparse
import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
from pathlib import Path
import struct
import subprocess
import time

from analyze_uikit_capture import write_png


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('out',type=Path)
    p.add_argument('--reuse-build',type=Path,help='reuse a prior binary only when compiler arguments and every source dependency match')
    p.add_argument('--effect',choices=('none','blur','glass'),default='none',help='actual UIVisualEffectView over the checker')
    p.add_argument('--window-root',action='store_true',help='render the entire attached UIWindow layer tree')
    p.add_argument('--window',action='store_true',help='attach view to a public UIWindow; log real lifecycle state')
    p.add_argument('--window-hold',type=int,choices=(0,20,45),default=0,help='hold our native probe window before offscreen rendering for a bounded compositor capture')
    p.add_argument('--frames',type=int,choices=(1,3),default=1)
    p.add_argument('--display-animate',action='store_true',help='native reference for alternating card geometry/transparency')
    p.add_argument('--display-frame',type=int,help='native 1179x2556 display-layout reference with this final frame marker')
    p.add_argument('--animate',action='store_true',help='move the card and change its opacity on the middle frame')
    p.add_argument('--guest-rasters',type=Path,help='native composition using captured guest A8 glyph inputs; still host evidence')
    p.add_argument('--diagnostic-unsplit',action='store_true')
    p.add_argument('--native-contract',action='store_true',help='test-only native device capability predicates matched to the forwarding profile')
    p.add_argument('--native-contract-before-ui',action='store_true',help='historical diagnostic: apply native capability overrides before UIKit prepares the scene')
    p.add_argument('--paired-native',choices=('first','last'),help='render one prepared layer tree through both native Metal and forwarding at identical frame times')
    p.add_argument('--native-query',action='append',default=[],help='restrict native contract overrides to named queries; repeat for a group')
    p.add_argument('--native-pipeline-delay-us',type=int,choices=(0,50000),default=0,help='bounded native asynchronous pipeline-creation delay, diagnostic only')
    libraries=p.add_mutually_exclusive_group()
    libraries.add_argument('--forwarded-library',type=Path,help='explicit host-rehearsal substitution, raw MTLB or fat library')
    libraries.add_argument('--native-library',type=Path,help='native host control using selected AIR instead of its default FAT library')
    a=p.parse_args()
    if a.window_root and (not a.window or a.display_frame is not None):p.error('window root requires window, no display reference')
    if a.window_hold and (not a.window or a.forwarded_library):p.error('window capture requires a native window control')
    if a.diagnostic_unsplit and not a.forwarded_library:p.error('unsplit requires forwarded control')
    if a.native_contract and a.forwarded_library:p.error('native contract requires a native control')
    if a.native_contract_before_ui and not a.native_contract:p.error('early native contract requires --native-contract')
    if a.paired_native and (not a.forwarded_library or a.native_contract_before_ui or a.display_frame is not None):p.error('paired control requires forwarded offscreen scene')
    if a.native_query and not a.native_contract:p.error('native query requires native contract')
    if a.native_pipeline_delay_us and a.forwarded_library:p.error('native pipeline delay requires native control')
    if a.display_animate and a.display_frame is None:p.error('display animation requires display frame')
    if a.display_frame is not None and (not 3<=a.display_frame<=1024 or a.frames!=1 or a.forwarded_library or a.animate):p.error('display reference requires native single image, frame 3..1024')
    if a.animate and a.frames!=3:p.error('animated control requires three frames')
    a.out=a.out.resolve();a.out.mkdir(exist_ok=False)
    executable=a.out/'test'
    if a.window:
        bundle=a.out/'UIKitProbe.app';executable=bundle/'Contents/MacOS/UIKitProbe'
        executable.parent.mkdir(parents=True)
        (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(
            CFBundleIdentifier='org.darwinvm.gpu.probe'+hashlib.sha256(str(a.out).encode()).hexdigest()[:12],CFBundleName='DVM UIKit Probe',
            CFBundleExecutable='UIKitProbe',CFBundlePackageType='APPL',
            CFBundleVersion='1',CFBundleShortVersionString='1.0',LSMinimumSystemVersion='27.0',
            NSHighResolutionCapable=True,UIApplicationSceneManifest=dict(
                UIApplicationSupportsMultipleScenes=True,UISceneConfigurations={
                    'UIWindowSceneSessionRoleApplication':[dict(UISceneConfigurationName='Probe',
                        UISceneClassName='UIWindowScene',UISceneDelegateClassName='DVMUIKitHostSceneDelegate')]}))))
    src=Path(__file__).resolve().parent
    sdk=Path(subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip())
    flags=['-target','arm64-apple-ios27.0-macabi','-isysroot',str(sdk),
        '-F',str(sdk/'System/iOSSupport/System/Library/Frameworks'),
        '-fobjc-arc','-fobjc-arc-exceptions','-O1','-Wall','-Wextra','-Werror',
        '-Wno-deprecated-declarations','-Wno-protocol','-Wno-objc-protocol-property-synthesis']
    sources=[src/'test_uikit_host.m'];env=os.environ.copy()
    for k in ('DVM_DRIVER_LIBRARY','DVM_REHEARSAL_AIR','DVM_UIKIT_DIAGNOSTIC_UNSPLIT','DVM_UIKIT_NATIVE_AIR','DVM_UIKIT_NATIVE_CONTRACT','DVM_UIKIT_NATIVE_QUERIES','DVM_UIKIT_NATIVE_PIPELINE_DELAY_US'):env.pop(k,None)
    env.pop('DVM_UIKIT_WINDOW_ROOT',None)
    if a.window_root:env['DVM_UIKIT_WINDOW_ROOT']='1'
    env.pop('DVM_UIKIT_WINDOW',None)
    if a.window:env['DVM_UIKIT_WINDOW']='1'
    env.pop('DVM_UIKIT_WINDOW_HOLD',None)
    if a.window_hold:env['DVM_UIKIT_WINDOW_HOLD']=str(a.window_hold)
    env['DVM_UIKIT_HOST_FRAMES']=str(a.frames)
    env.pop('DVM_UIKIT_HOST_ANIMATE',None)
    env.pop('DVM_UIKIT_DISPLAY_FRAME',None)
    env.pop('DVM_UIKIT_DISPLAY_ANIMATE',None)
    if a.display_animate:env['DVM_UIKIT_DISPLAY_ANIMATE']='1'
    if a.display_frame is not None:env['DVM_UIKIT_DISPLAY_FRAME']=str(a.display_frame)
    env.pop('DVM_UIKIT_GUEST_RASTERS',None)
    env['DVM_UIKIT_EFFECT']=str(('none','blur','glass').index(a.effect))
    if a.animate:env['DVM_UIKIT_HOST_ANIMATE']='1'
    if a.diagnostic_unsplit:env['DVM_UIKIT_DIAGNOSTIC_UNSPLIT']='1'
    if a.native_contract:env['DVM_UIKIT_NATIVE_CONTRACT']='1'
    env.pop('DVM_UIKIT_NATIVE_CONTRACT_BEFORE_UI',None)
    if a.native_contract_before_ui:env['DVM_UIKIT_NATIVE_CONTRACT_BEFORE_UI']='1'
    env.pop('DVM_UIKIT_PAIRED_NATIVE',None)
    if a.paired_native:env['DVM_UIKIT_PAIRED_NATIVE']=a.paired_native
    if a.native_pipeline_delay_us:env['DVM_UIKIT_NATIVE_PIPELINE_DELAY_US']=str(a.native_pipeline_delay_us)
    if a.native_query:
        names=set(re.findall(r'^ [BU]\((\w+),', (src/'driver_capabilities.h').read_text(), re.M))|{'supportsFamily:','supportsFeatureSet:','supportsTextureSampleCount:'}
        if not set(a.native_query)<=names:p.error('unknown native query')
        env['DVM_UIKIT_NATIVE_QUERIES']=','.join(a.native_query)
    metadata=dict(frames=a.frames,diagnostic_unsplit=a.diagnostic_unsplit,scope='host-catalyst-rehearsal-not-exact-guest',forwarded=bool(a.forwarded_library))
    metadata['frame_time_step_seconds']=1/60
    metadata['animate']=a.animate
    metadata['effect']=a.effect
    metadata['window_requested']=a.window
    metadata['window_root']=a.window_root
    metadata['paired_native_order']=a.paired_native
    if a.paired_native:metadata['paired_native_control']=dict(capabilities='supported forwarding predicates after actual backend validation',scope='native-device-class overrides after UIKit prepares one shared layer tree',library='same selected AIR as forwarding',layer_ownership='explicit transfer between CARenderer contexts')
    metadata['window_hold_seconds']=a.window_hold
    metadata['display_frame']=a.display_frame
    metadata['display_animate']=a.display_animate
    metadata['width']=1179 if a.display_frame is not None else 320
    metadata['height']=2556 if a.display_frame is not None else 480
    if a.guest_rasters:
        folder=a.guest_rasters.resolve();inputs=json.loads((folder/'inputs.json').read_text())
        if inputs.get('scope')!='exact-guest-generated-glyph-inputs-not-rendered-output' or len(inputs.get('labels',[]))!=3:raise ValueError('guest raster provenance')
        for label in inputs['labels']:
            if label['name'] not in ('title','caption','button'):raise ValueError('glyph fixture name')
            raw=(folder/(label['name']+'.a8')).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=label['source_sha256'] or len(raw)!=label['width']*label['height']:raise ValueError('glyph input integrity')
        env['DVM_UIKIT_GUEST_RASTERS']=str(folder);metadata['guest_rasters']=inputs
    if a.forwarded_library or a.native_library:
        library=(a.forwarded_library or a.native_library).resolve();raw=library.read_bytes()
        candidates=[]
        if raw[:4]==bytes.fromhex('cafebabe'):
            count=struct.unpack_from('>I',raw,4)[0]
            if not 0<count<=128 or len(raw)<8+count*20:raise ValueError('fat library table')
            for i in range(count):
                off,size=struct.unpack_from('>II',raw,8+i*20+8)
                if off>len(raw) or size>len(raw)-off:raise ValueError('fat library extent')
                if raw[off:off+4]==b'MTLB':candidates.append(raw[off:off+size])
        else:candidates=[raw]
        if len(candidates)!=1:raise ValueError('requires unique host MTLB cache')
        cache=candidates[0]
        if len(cache)<88 or cache[:4]!=b'MTLB' or struct.unpack_from('<Q',cache,16)[0]!=len(cache):raise ValueError('MTLB header')
        (a.out/'library.metallib').write_bytes(cache)
        metadata.update(library=str(library),source_sha256=hashlib.sha256(raw).hexdigest(),
            selected_sha256=hashlib.sha256(cache).hexdigest(),selected_bytes=len(cache))
    if a.forwarded_library:
        env['DVM_DRIVER_LIBRARY']=str(a.out/'library.metallib');env['DVM_REHEARSAL_AIR']=str(library)
        sources.append(src/'driver_guest.m');flags+=['-DDVM_UIKIT_FORWARDED','-DDVM_CA_REHEARSAL']
        if a.paired_native:env['DVM_UIKIT_NATIVE_AIR']=str(a.out/'library.metallib')
    if a.native_library:env['DVM_UIKIT_NATIVE_AIR']=str(a.out/'library.metallib')
    metadata['native_air_override']=bool(a.native_library)
    metadata['native_contract_override']=a.native_contract
    metadata['native_contract_stage']='before-ui' if a.native_contract_before_ui else 'before-offscreen-renderer'
    metadata['native_query_overrides']=a.native_query
    metadata['native_pipeline_delay_us']=a.native_pipeline_delay_us
    argv=['xcrun','clang',*flags,*map(str,sources),'-framework','Foundation','-framework','Metal',
        '-framework','QuartzCore','-framework','CoreGraphics','-framework','UIKit','-framework','IOSurface','-o',str(executable)]
    metadata['executable']=str(executable)
    metadata['build_argv']=argv
    metadata['validation_environment']={k:env[k] for k in ('MTL_DEBUG_LAYER','MTL_SHADER_VALIDATION') if k in env}
    dependencies={}
    for source in sources:
        scan=subprocess.check_output(['xcrun','clang',*flags,'-MM','-MT','dependencies',str(source)],text=True)
        for name in shlex.split(scan.replace('\\\n','').split(':',1)[1]):
            path=Path(name).resolve()
            if path.is_relative_to(src):dependencies[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    metadata['source_sha256_by_path']=dependencies
    started=time.monotonic()
    if a.reuse_build:
        previous=json.loads((a.reuse_build/'manifest.json').read_text())
        source=Path(previous['executable'])
        if previous.get('source_sha256_by_path')!=dependencies or previous.get('build_argv',[])[:-1]!=argv[:-1] or hashlib.sha256(source.read_bytes()).hexdigest()!=previous.get('binary_sha256'):
            raise ValueError('reused build arguments, dependencies or binary identity differ')
        shutil.copyfile(source,executable);executable.chmod(0o755)
        metadata['reused_build_from']=str(a.reuse_build.resolve())
        (a.out/'build.log').write_text('Reused binary after exact argument, dependency and hash checks.\n')
    else:
        with (a.out/'build.log').open('wb') as log:subprocess.run(argv,stdout=log,stderr=log,check=True,timeout=60)
    metadata['build_seconds']=time.monotonic()-started
    metadata['binary_sha256']=hashlib.sha256(executable.read_bytes()).hexdigest()
    started=time.monotonic()
    try:
        with (a.out/'result.log').open('wb') as log:
            r=subprocess.run([str(executable),str(a.out)],env=env,stdout=log,stderr=log,timeout=30+a.window_hold)
        metadata['exit']=r.returncode
        r.check_returncode()
        for name in ('gpu','cpu'):
            data=(a.out/(name+'.bgra')).read_bytes();write_png(a.out/(name+'.png'),data,metadata['width'],metadata['height'])
            metadata[name+'_sha256']=hashlib.sha256(data).hexdigest()
        if a.paired_native:
            comparisons=[]
            for i in range(a.frames):
                native=(a.out/f'paired-native-frame-{i}.bgra').read_bytes();forwarded=(a.out/f'gpu-frame-{i}.bgra').read_bytes()
                if len(native)!=320*480*4 or len(forwarded)!=len(native):raise ValueError('paired output extent')
                delta=[abs(x-y) for x,y in zip(native,forwarded)]
                comparisons.append(dict(frame=i,exact_match=native==forwarded,channels_over_2=sum(d>2 for d in delta),max_error=max(delta),native_sha256=hashlib.sha256(native).hexdigest(),forwarded_sha256=hashlib.sha256(forwarded).hexdigest()))
            metadata['paired_comparison']=dict(scope='same-host-layer-tree-and-frame-time-native-versus-forwarded-not-guest-evidence',all_frames_exact=all(f['exact_match'] for f in comparisons),frames=comparisons)
            write_png(a.out/'paired-native.png',native,320,480)
    except Exception as error:
        metadata['execution_error']=type(error).__name__+': '+str(error)
        raise
    finally:
        metadata['execution_seconds']=time.monotonic()-started
        (a.out/'manifest.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps(metadata))


if __name__=='__main__':main()
