#!/usr/bin/env python3
"""Build the no-modem service using verified exact-guest linker declarations."""
import argparse,json,plistlib,shutil,subprocess
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('out',type=Path);p.add_argument('--stubs',type=Path);p.add_argument('--cache',type=Path,default=Path('/Users/jdolbe1/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e'));a=p.parse_args()
    a.out.mkdir(exist_ok=False)
    if a.stubs:shutil.copytree(a.stubs,a.out/'stubs')
    repo=Path(__file__).resolve().parents[2]
    sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
    flags=['-target','arm64e-apple-ios27.0','-isysroot',sdk,'-Wno-incompatible-sysroot','-fobjc-arc','-fobjc-arc-exceptions','-fno-objc-msgsend-selector-stubs','-O1','-Wall','-Wextra','-Werror']
    commands=[]
    def run(v):commands.append(v);subprocess.run(v,check=True)
    run(['xcrun','clang',*flags,'-c',str(repo/'tools/comm/plan_service.m'),'-o',str(a.out/'service.o')])
    object_imports=a.out/'object-imports.txt';object_imports.write_bytes(subprocess.check_output(['nm','-u',str(a.out/'service.o')]))
    run(['python3',str(repo/'tools/gpu/verify_guest_imports.py'),'--cache',str(a.cache),'--output',str(a.out/'object-providers.tsv'),str(object_imports)])
    groups={}
    for row in (a.out/'object-providers.tsv').read_text().splitlines()[1:]:
        symbol,providers=row.split('\t');providers=providers.split(';')
        if '/usr/lib/libobjc.A.dylib' in providers:install='/usr/lib/libobjc.A.dylib';rel='usr/lib/libobjc.tbd'
        elif all(v.startswith('/usr/lib/system/') for v in providers):install='/usr/lib/libSystem.B.dylib';rel='usr/lib/libSystem.tbd'
        else:
            provider=providers[0]
            if '/Foundation.framework/' in provider:framework='Foundation'
            elif '/CoreFoundation.framework/' in provider:framework='CoreFoundation'
            else:raise ValueError('unreviewed import provider: '+row)
            install=f'/System/Library/Frameworks/{framework}.framework/{framework}'
            rel=f'System/Library/Frameworks/{framework}.framework/{framework}.tbd'
        entry=groups.setdefault(rel,dict(install=install,symbols=[]));entry['symbols'].append(symbol)
    for rel,entry in groups.items():
        names=', '.join('"'+n+'"' for n in sorted(entry['symbols']))
        (a.out/'stubs'/rel).parent.mkdir(parents=True,exist_ok=True)
        (a.out/'stubs'/rel).write_text('--- !tapi-tbd\ntbd-version: 4\ntargets: [ arm64e-ios ]\ninstall-name: '+entry['install']+'\nexports:\n  - targets: [ arm64e-ios ]\n    symbols: [ '+names+' ]\n...\n')
    (a.out/'link-providers.json').write_text(json.dumps(groups,indent=2))
    binary=a.out/'dvm-cellular-plan'
    run(['xcrun','clang',*flags,str(a.out/'service.o'),'-F',str(a.out/'stubs/System/Library/Frameworks'),'-L',str(a.out/'stubs/usr/lib'),'-framework','Foundation','-framework','CoreFoundation','-lobjc','-o',str(binary)])
    ent=a.out/'entitlements.plist';ent.write_bytes(plistlib.dumps({'platform-application':True}))
    run(['codesign','--force','--sign','-','--timestamp=none','--entitlements',str(ent),str(binary)])
    run(['codesign','--verify','--strict',str(binary)])
    sig=subprocess.check_output(['codesign','-d','--verbose=4',str(binary)],stderr=subprocess.STDOUT,text=True)
    (a.out/'codesign.txt').write_text(sig)
    (a.out/'hashes.txt').write_text(next(x.split('=')[1] for x in sig.splitlines() if x.startswith('CDHash='))+'\n')
    run(['python3',str(repo/'build_tc.py'),str(a.out/'hashes.txt'),str(a.out/'service.tc')])
    imports=a.out/'imports.txt';imports.write_bytes(subprocess.check_output(['nm','-u',str(binary)]))
    run(['python3',str(repo/'tools/gpu/verify_guest_imports.py'),'--cache',str(a.cache),'--output',str(a.out/'imports.tsv'),str(imports)])
    (a.out/'commands.json').write_text(json.dumps(commands,indent=2));shutil.copy(repo/'tools/comm/plan_service.m',a.out)
if __name__=='__main__':main()
