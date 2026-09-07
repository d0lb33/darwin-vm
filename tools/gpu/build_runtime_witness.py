#!/usr/bin/env python3
"""Build a code-changed driver fixture after VM start, retaining helper/backend.

The factory emits a revision witness; rendering functionality is unchanged.
Only its bundle is a runtime deployment input. Inherited helper.tc is not a
trust cache for the new driver and must not be staged as one.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('base',type=Path);p.add_argument('out',type=Path)
    p.add_argument('--revision',type=int,required=True)
    a=p.parse_args()
    if not 1<=a.revision<=1000000:p.error('revision outside fixture bounds')
    a.base=a.base.resolve();a.out=a.out.resolve()
    start=time.monotonic();created=time.time();shutil.copytree(a.base,a.out)
    source=a.out/'driver_guest.m';original=source.read_text()
    needle='id<MTLDevice> DVMCreateBinaryMetalDevice(DVMMetalRPC rpc) {\n'
    if original.count(needle)!=1 or 'GPU_LOAD_RUNTIME_REVISION' in original:raise ValueError('requires clean factory source')
    source.write_text('#include <stdio.h>\n'+original.replace(needle,needle+
        f'    fprintf(stderr,"GPU_LOAD_RUNTIME_REVISION revision={a.revision} factory=entered\\n");\n'))
    sdk=subprocess.check_output(['xcrun','--show-sdk-path'],text=True).strip()
    guest=['-target','arm64-apple-ios27.0','-isysroot',sdk,'-Wno-incompatible-sysroot',
           '-fno-objc-msgsend-selector-stubs','-Wno-protocol','-Wno-objc-protocol-property-synthesis']
    common=['-fobjc-arc','-fobjc-arc-exceptions','-O1','-Wall','-Wextra','-Werror','-Wno-deprecated-declarations']
    commands=[['xcrun','clang',*common,*guest,'-c',str(source),'-o',str(a.out/'driver_guest.o')],
        ['xcrun','clang',*guest,'-F',str(a.out/'stubs/System/Library/Frameworks'),'-L',str(a.out/'stubs/usr/lib'),
         '-dynamiclib','-Wl,-install_name,/usr/local/libexec/DVMProxy.bundle/DVMProxy',str(a.out/'driver_guest.o'),
         '-framework','Foundation','-framework','CoreFoundation','-framework','IOSurface','-framework','Metal',
         '-lobjc','-o',str(a.out/'DVMProxy.bundle/DVMProxy')],
        ['codesign','--force','--sign','-','--timestamp=none',str(a.out/'DVMProxy.bundle')],
        ['codesign','--verify','--strict',str(a.out/'DVMProxy.bundle')]]
    for c in commands:subprocess.run(c,check=True)
    nm=subprocess.check_output(['nm','-u',str(a.out/'DVMProxy.bundle/DVMProxy')]);(a.out/'DVMProxy.nm-u').write_bytes(nm)
    subprocess.run(['python3',str(Path(__file__).with_name('verify_guest_imports.py')),'--output',str(a.out/'revision-imports.tsv'),str(a.out/'DVMProxy.nm-u')],check=True)
    unchanged={n:(a.out/n).read_bytes()==(a.base/n).read_bytes() for n in ('dvm-gpu-load','driver_host')}
    if not all(unchanged.values()):raise ValueError('fixture changed helper/backend')
    record=dict(commands=commands,seconds=time.monotonic()-start,revision=a.revision,created_unix=created,
        built_unix=time.time(),base=str(a.base),unchanged=unchanged,
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        driver_sha256=hashlib.sha256((a.out/'DVMProxy.bundle/DVMProxy').read_bytes()).hexdigest())
    (a.out/'revision-runtime.json').write_text(json.dumps(record,indent=2)+'\n')
    shutil.copy2(__file__,a.out/Path(__file__).name);print(json.dumps(record))


if __name__=='__main__':main()
