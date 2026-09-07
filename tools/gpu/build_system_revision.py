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
    a=p.parse_args()
    if not 1<=a.revision<=1000000:p.error('revision out of bounds')
    a.base=a.base.resolve();a.out=a.out.resolve()
    if not json.loads((a.base/'build.json').read_text()).get('runtime_probe'):p.error('requires opted-in loader build')
    a.out.mkdir(exist_ok=False);shutil.copytree(a.base/'stubs',a.out/'stubs')
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
    stub=a.out/'stubs/System/Library/Frameworks/Foundation.framework/Foundation.tbd'
    text=stub.read_text();symbol='"_OBJC_CLASS_$_NSURL"'
    if symbol not in text:stub.write_text(text.replace('symbols: [ ','symbols: [ '+symbol+', '))
    run(['xcrun','clang',*flags,'-dynamiclib','-Wl,-install_name,@rpath/DVMProxy.bundle/DVMProxy',str(a.out/'revision.o'),
        '-F',str(a.out/'stubs/System/Library/Frameworks'),'-L',str(a.out/'stubs/usr/lib'),
        '-framework','Foundation','-framework','CoreFoundation','-framework','Metal','-framework','IOSurface','-lobjc','-o',str(bundle/'DVMProxy')])
    (bundle/'Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier=f'org.darwin-vm.revision{a.revision}',CFBundleExecutable='DVMProxy',CFBundlePackageType='BNDL',CFBundleVersion=str(a.revision))))
    run(['codesign','--force','--sign','-','--timestamp=none',str(bundle)])
    run(['python3',str(src/'sign_linked_revision.py'),str(bundle),str(a.out/'linked'),'--parent',str(a.base/'backboardd')])
    imports=a.out/'imports.nm-u';imports.write_bytes(subprocess.check_output(['nm','-u',str(bundle/'DVMProxy')]))
    run(['python3',str(src/'verify_guest_imports.py'),'--output',str(a.out/'imports.tsv'),str(imports)])
    for f in src.iterdir():
        if f.suffix in ('.m','.h','.inc','.py'):shutil.copyfile(f,a.out/f.name)
    (a.out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
    print(a.out)


if __name__=='__main__':main()
