#!/usr/bin/env python3
"""Build a distinct arm64e driver image linked to an opted-in backboardd loader.

Classes have revision-specific names so the image can coexist with the boot
bootstrap. This does not unload or replace any registered device.
"""
import argparse
import json
import plistlib
from pathlib import Path
import re
import shutil
import subprocess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('base',type=Path,help='build_system_bootstrap.py --runtime-probe output')
    p.add_argument('out',type=Path)
    p.add_argument('--revision',type=int,default=1)
    p.add_argument('--parent',type=Path,help='executable whose CodeDirectory hash the linkage signature names; default is the loader build\'s backboardd')
    a=p.parse_args()
    if not 1<=a.revision<=1000000:p.error('revision out of bounds')
    a.base=a.base.resolve();a.out=a.out.resolve()
    build=json.loads((a.base/'build.json').read_text())
    if not (build.get('runtime_probe') or build.get('session_reload')):p.error('requires opted-in loader build')
    a.out.mkdir(exist_ok=False);shutil.copytree(a.base/'stubs',a.out/'stubs')
    for copied in (a.out/'stubs').rglob('*'):
        if copied.is_file():copied.chmod(copied.stat().st_mode|0o200)
    src=Path(__file__).resolve().parent;commands=[]
    def run(cmd):commands.append(cmd);subprocess.run(cmd,check=True)
    sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
    classes=sorted(set(re.findall(r'@interface\s+(DVM\w+)', '\n'.join(f.read_text() for f in src.iterdir() if f.suffix in ('.m','.inc')))))
    flags=['-target','arm64e-apple-ios27.0','-isysroot',sdk,'-Wno-incompatible-sysroot',
        '-fobjc-arc','-fobjc-arc-exceptions','-O1','-Wall','-Wextra','-Werror',
        '-Wno-deprecated-declarations','-Wno-protocol','-Wno-objc-protocol-property-synthesis','-fno-objc-msgsend-selector-stubs']
    flags+=['-D'+n+f'=DVMRevision{a.revision}'+n[3:] for n in classes]
    wrapper=a.out/'revision.m'
    wrapper.write_text('#include "driver_guest.m"\nuint64_t DVMRevisionNumber(void){return '+str(a.revision)+';}\n')
    bundle=a.out/'DVMProxy.bundle';bundle.mkdir()
    run(['xcrun','clang',*flags,'-I',str(src),'-c',str(wrapper),'-o',str(a.out/'revision.o')])
    # Register allocation changes can emit additional ARC register helpers.
    # Declare emitted imports, then verify them against the exact guest exports.
    symbols=set(subprocess.check_output(['nm','-u',str(a.out/'revision.o')],text=True).split())
    objc=a.out/'stubs/usr/lib/libobjc.tbd';text=objc.read_text()
    objc_runtime_prefixes = ('_objc_', '_class_', '_protocol_')
    names=sorted(s for s in symbols if s.startswith(objc_runtime_prefixes) and '"'+s+'"' not in text)
    objc.write_text(text.replace('symbols: [ ','symbols: [ '+''.join('"'+s+'", ' for s in names)))
    system=a.out/'stubs/usr/lib/libSystem.tbd';text=system.read_text()
    names=sorted(s for s in symbols if s in ('_pthread_threadid_np',) and '"'+s+'"' not in text)
    if names:system.write_text(text.replace('symbols: [ ','symbols: [ '+''.join('"'+s+'", ' for s in names)))
    stub=a.out/'stubs/System/Library/Frameworks/Foundation.framework/Foundation.tbd'
    text=stub.read_text()
    foundation_classes=('_OBJC_CLASS_$_NSURL', '_OBJC_CLASS_$_NSMethodSignature')
    names=sorted(s for s in foundation_classes if s in symbols and '"'+s+'"' not in text)
    if names:stub.write_text(text.replace('symbols: [ ','symbols: [ '+''.join('"'+s+'", ' for s in names)))
    run(['xcrun','clang',*flags,'-dynamiclib','-Wl,-install_name,@rpath/DVMProxy.bundle/DVMProxy',str(a.out/'revision.o'),
        '-F',str(a.out/'stubs/System/Library/Frameworks'),'-L',str(a.out/'stubs/usr/lib'),
        '-framework','Foundation','-framework','CoreFoundation','-framework','Metal','-framework','IOSurface','-lobjc','-o',str(bundle/'DVMProxy')])
    (bundle/'Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier=f'org.darwin-vm.revision{a.revision}',CFBundleExecutable='DVMProxy',CFBundlePackageType='BNDL',CFBundleVersion=str(a.revision))))
    run(['codesign','--force','--sign','-','--timestamp=none',str(bundle)])
    run(['python3',str(src/'sign_linked_revision.py'),str(bundle),str(a.out/'linked'),'--parent',str((a.parent or a.base/'backboardd').resolve())])
    imports=a.out/'imports.nm-u';imports.write_bytes(subprocess.check_output(['nm','-u',str(bundle/'DVMProxy')]))
    run(['python3',str(src/'verify_guest_imports.py'),'--output',str(a.out/'imports.tsv'),str(imports)])
    for f in src.iterdir():
        if f.suffix in ('.m','.h','.inc','.py'):shutil.copyfile(f,a.out/f.name)
    (a.out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
    print(a.out)


if __name__=='__main__':main()
