#!/usr/bin/env python3
"""Incremental driver/backend revision; retain the already-trusted test helper."""
import argparse
import hashlib
import json
import plistlib
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('base',type=Path);p.add_argument('out',type=Path)
    p.add_argument('--bootstrap',action='store_true',help='incrementally rebuild the pinned mode-3 consumer supervisor; requires reinstall/reboot')
    p.add_argument('--development-loader',action='store_true',help='add the dedicated test entitlement; requires --bootstrap and a new boot trust cache')
    p.add_argument('--compilation-loader',action='store_true',help='opt-in TXM compilation-hash authorization entitlement; requires --bootstrap --development-loader')
    a=p.parse_args()
    if a.development_loader and not a.bootstrap:p.error('development loader requires bootstrap revision')
    if a.compilation_loader and not (a.bootstrap and a.development_loader):p.error('compilation loader requires bootstrap development revision')
    a.base=a.base.resolve();a.out=a.out.resolve()
    repo=Path(__file__).resolve().parents[2];source=repo/'tools/gpu'
    started=time.monotonic();shutil.copytree(a.base,a.out)
    sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
    common=['-fobjc-arc','-fobjc-arc-exceptions','-O1','-Wall','-Wextra','-Werror','-Wno-deprecated-declarations']
    guest=['-target','arm64-apple-ios27.0','-isysroot',sdk,'-Wno-incompatible-sysroot',
           '-fno-objc-msgsend-selector-stubs','-Wno-protocol','-Wno-objc-protocol-property-synthesis']
    record=dict(base=str(a.base),sdk=sdk,components={},helper_sha256=hashlib.sha256((a.base/'dvm-gpu-load').read_bytes()).hexdigest())
    for name,flags in [('driver_guest',common+guest),('driver_host',common)]:
        scan=subprocess.check_output(['xcrun','clang',*flags,'-MM','-MT','dependencies',str(source/(name+'.m'))],text=True)
        paths=[Path(x).resolve() for x in shlex.split(scan.replace('\\\n','').split(':',1)[1])]
        deps={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in paths if f.is_relative_to(repo)}
        changed=[f for f,h in deps.items() if not (a.base/Path(f).name).exists() or hashlib.sha256((a.base/Path(f).name).read_bytes()).hexdigest()!=h]
        t=time.monotonic()
        if changed:
            if name=='driver_guest':
                subprocess.run(['xcrun','clang',*flags,'-c',str(source/'driver_guest.m'),'-o',str(a.out/'driver_guest.o')],check=True)
                symbols=subprocess.check_output(['nm','-u',str(a.out/'driver_guest.o')],text=True).split()
                stub=a.out/'stubs/usr/lib/libobjc.tbd';text=stub.read_text()
                added=[s for s in symbols if s.startswith('_objc_') and '"'+s+'"' not in text]
                text=text.replace('symbols: [ ','symbols: [ '+''.join('"'+s+'", ' for s in added));stub.write_text(text)
                subprocess.run(['xcrun','clang',*guest,'-F',str(a.out/'stubs/System/Library/Frameworks'),'-L',str(a.out/'stubs/usr/lib'),
                    '-dynamiclib','-Wl,-install_name,/usr/local/libexec/DVMProxy.bundle/DVMProxy',str(a.out/'driver_guest.o'),
                    '-framework','Foundation','-framework','CoreFoundation','-framework','IOSurface','-framework','Metal','-lobjc',
                    '-o',str(a.out/'DVMProxy.bundle/DVMProxy')],check=True)
                subprocess.run(['codesign','--force','--sign','-','--timestamp=none',str(a.out/'DVMProxy.bundle')],check=True)
                subprocess.run(['codesign','--verify','--strict',str(a.out/'DVMProxy.bundle')],check=True)
                signature=subprocess.run(['codesign','-d','-vvv',str(a.out/'DVMProxy.bundle')],capture_output=True,text=True,check=True).stderr
                (a.out/'DVMProxy.codesign.txt').write_text(signature)
                (a.out/'DVMProxy.nm-u').write_bytes(subprocess.check_output(['nm','-u',str(a.out/'DVMProxy.bundle/DVMProxy')]))
                subprocess.run(['python3',str(source/'verify_guest_imports.py'),'--output',str(a.out/'revision-imports.tsv'),str(a.out/'DVMProxy.nm-u')],check=True)
            else:
                subprocess.run(['xcrun','clang',*flags,str(source/'driver_host.m'),'-framework','Metal','-framework','Foundation','-o',str(a.out/'driver_host')],check=True)
        for f in deps:shutil.copyfile(f,a.out/Path(f).name)
        record['components'][name]=dict(rebuilt=bool(changed),changed=changed,dependencies=deps,seconds=time.monotonic()-t)
    if a.bootstrap:
        if any((a.base/file).read_text().strip()!=value for file,value in (
                ('transport-mode.txt','--mmio-present-pool'),('consumer-probe.txt','1'),('test-runner.txt','1'))):
            raise ValueError('bootstrap revision requires the mode-3 persistent consumer base')
        frames=int((a.base/'consumer-frames.txt').read_text())
        defines=['-DDVM_DRIVER_MMIO','-DDVM_DRIVER_BINARY','-DDVM_DRIVER_PRESENT',
                 '-DDVM_SHARED_SURFACE','-DDVM_SERVICE_POOL_CONTRACT','-DDVM_CA_PROBE',
                 '-DDVM_TEST_RUNNER',f'-DDVM_CA_FRAMES={frames}']
        entitlements_path=a.out/'entitlements.plist'
        before_entitlements=entitlements_path.read_bytes()
        entitlements=plistlib.loads(before_entitlements)
        if a.development_loader:
            entitlements['org.darwin-vm.development-loader']=True
            # Exact TXM 217.0.2, 0xfffffff017033618: the target address
            # space needs this entitlement as well as Developer Mode.
            entitlements['get-task-allow']=True
        if a.compilation_loader:
            entitlements['com.apple.private.amfi.can-load-cdhash']=True
        entitlements_path.write_bytes(plistlib.dumps(entitlements))
        helper_changed=entitlements_path.read_bytes()!=before_entitlements
        record['development_loader_entitlement']=bool(entitlements.get('org.darwin-vm.development-loader'))
        record['compilation_loader_entitlement']=bool(entitlements.get('com.apple.private.amfi.can-load-cdhash'))
        for name in ('driver_probe','driver_workload'):
            flags=common+guest+defines+(['-O3'] if name=='driver_probe' else [])
            scan=subprocess.check_output(['xcrun','clang',*flags,'-MM','-MT','dependencies',str(source/(name+'.m'))],text=True)
            paths=[Path(x).resolve() for x in shlex.split(scan.replace('\\\n','').split(':',1)[1])]
            deps={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in paths if f.is_relative_to(repo)}
            changed=[f for f,h in deps.items() if not (a.base/Path(f).name).exists() or hashlib.sha256((a.base/Path(f).name).read_bytes()).hexdigest()!=h]
            t=time.monotonic()
            if changed:
                subprocess.run(['xcrun','clang',*flags,'-c',str(source/(name+'.m')),'-o',str(a.out/(name+'.o'))],check=True)
                helper_changed=True
            for f in deps:shutil.copyfile(f,a.out/Path(f).name)
            record['components'][name]=dict(rebuilt=bool(changed),changed=changed,dependencies=deps,seconds=time.monotonic()-t)
        if helper_changed:
            t=time.monotonic();stub=a.out/'stubs/usr/lib/libobjc.tbd';text=stub.read_text()
            symbols=set()
            for name in ('driver_probe','driver_workload'):
                symbols.update(subprocess.check_output(['nm','-u',str(a.out/(name+'.o'))],text=True).split())
            added=[symbol for symbol in sorted(symbols) if symbol.startswith('_objc_') and '"'+symbol+'"' not in text]
            stub.write_text(text.replace('symbols: [ ','symbols: [ '+''.join('"'+symbol+'", ' for symbol in added)))
            binary=a.out/'dvm-gpu-load'
            subprocess.run(['xcrun','clang',*guest,'-F',str(a.out/'stubs/System/Library/Frameworks'),'-L',str(a.out/'stubs/usr/lib'),
                str(a.out/'driver_probe.o'),str(a.out/'driver_workload.o'),'-framework','Foundation','-framework','CoreFoundation',
                '-framework','IOSurface','-framework','Metal','-framework','QuartzCore','-framework','CoreGraphics','-lobjc','-o',str(binary)],check=True)
            subprocess.run(['codesign','--force','--sign','-','--timestamp=none','--entitlements',str(a.out/'entitlements.plist'),str(binary)],check=True)
            subprocess.run(['codesign','--verify','--strict',str(binary)],check=True)
            signature=subprocess.run(['codesign','-d','-vvv',str(binary)],capture_output=True,text=True,check=True).stderr
            (a.out/'dvm-gpu-load.codesign.txt').write_text(signature)
            (a.out/'dvm-gpu-load.nm-u').write_bytes(subprocess.check_output(['nm','-u',str(binary)]))
            subprocess.run(['python3',str(source/'verify_guest_imports.py'),'--output',str(a.out/'bootstrap-imports.tsv'),str(a.out/'dvm-gpu-load.nm-u')],check=True)
            record['helper_link_sign_seconds']=time.monotonic()-t
        record['helper_sha256']=hashlib.sha256((a.out/'dvm-gpu-load').read_bytes()).hexdigest()
        record['bootstrap_rebuilt']=helper_changed
    hashes=[]
    for name in ['DVMProxy','dvm-gpu-load']:
        hashes.extend(re.findall(r'^CDHash=(\w+)$',(a.out/(name+'.codesign.txt')).read_text(),re.M))
    if len(hashes)!=2:raise ValueError('signing identities')
    (a.out/'hashes.txt').write_text('\n'.join(hashes)+'\n')
    subprocess.run(['python3',str(repo/'build_tc.py'),str(a.out/'hashes.txt'),str(a.out/'helper.tc')],check=True)
    record.update(seconds=time.monotonic()-started,driver_cdhash=hashes[0],
                  scope='only changed dependencies rebuilt; host test client inherited; bootstrap changes require reinstall/reboot' if a.bootstrap else 'only changed driver/backend dependencies rebuilt; helper and host client test executable inherited from base')
    (a.out/'revision.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record))


if __name__=='__main__':main()
