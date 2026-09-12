#!/usr/bin/env python3
"""Build an isolated backboardd Metal bootstrap, preserving executable text."""
import argparse,hashlib,json,plistlib,re,shutil,struct,subprocess,time
from pathlib import Path

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def instruction_sections(b):
    out={};o=32
    for _ in range(struct.unpack_from('<I',b,16)[0]):
        c,n=struct.unpack_from('<II',b,o)
        if c==0x19:
            for i in range(struct.unpack_from('<I',b,o+64)[0]):
                s=o+72+i*80
                name=b[s+16:s+32].rstrip(b'\0').decode()+'.'+b[s:s+16].rstrip(b'\0').decode()
                size,offset=struct.unpack_from('<QI',b,s+40);flags=struct.unpack_from('<I',b,s+64)[0]
                if flags&0x80000400:out[name]=hashlib.sha256(b[offset:offset+size]).hexdigest()
        o+=n
    return out
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('base',type=Path);p.add_argument('backboardd',type=Path);p.add_argument('out',type=Path)
    p.add_argument('--runtime-probe',action='store_true',help='bounded arm64e runtime loading probe inside backboardd; does not replace/unload the boot driver')
    p.add_argument('--surface-pin-probe',action='store_true',help='audit the first actual compositor IOSurface and exercise the opt-in kernel pin/complete probe')
    p.add_argument('--surface-import',action='store_true',help='opt-in retained compositor page imports; requires matching registry kernel and QEMU')
    p.add_argument('--session-reload',action='store_true',help='opt-in host-controlled revision staging, generation announcement and retirement; process replacement only, never a live unload')
    p.add_argument('--poster',type=Path,help='also patch this MercuryPosterExtension executable as a second transport client (wallpaper), same bundle dependency')
    a=p.parse_args();a.out=a.out.resolve();a.out.mkdir(exist_ok=False)
    source=Path(__file__).resolve().parent;repo=source.parents[1];start=time.monotonic()
    shutil.copytree(a.base/'stubs',a.out/'stubs')
    # Preserved evidence trees are intentionally read-only.  The copied TAPI
    # stubs are build scratch: make those copies writable before adapting their
    # architecture and adding imports emitted by this revision.
    for copied in (a.out/'stubs').rglob('*'):
        if copied.is_file():copied.chmod(copied.stat().st_mode|0o200)
    # Exact guest DYLD OS_REASON in CA_SYSTEM_BOOT_GUEST2: an arm64e
    # backboardd rejects the arm64 bundle accepted by the old arm64 runner.
    for stub in (a.out/'stubs').rglob('*.tbd'):
        stub.write_text(stub.read_text().replace('arm64-ios','arm64e-ios'))
    sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
    flags=['-target','arm64e-apple-ios27.0','-isysroot',sdk,'-Wno-incompatible-sysroot','-fobjc-arc','-fobjc-arc-exceptions',
           '-O1','-Wall','-Wextra','-Werror','-Wno-deprecated-declarations','-Wno-protocol','-Wno-objc-protocol-property-synthesis','-fno-objc-msgsend-selector-stubs']
    if a.runtime_probe:flags.append('-DDVM_BOOT_RUNTIME_PROBE')
    if a.session_reload:flags.append('-DDVM_BOOT_SESSION_RELOAD')
    if a.surface_pin_probe:flags.append('-DDVM_SURFACE_PIN_PROBE')
    if a.surface_import:flags.append('-DDVM_SURFACE_IMPORT')
    commands=[]
    def run(cmd,**kw):commands.append(cmd);return subprocess.run(cmd,check=True,**kw)
    obj=a.out/'system_bootstrap.o'
    run(['xcrun','clang',*flags,'-c',str(source/'system_bootstrap.m'),'-o',str(obj)])
    symbols=set(subprocess.check_output(['nm','-u',str(obj)],text=True).split())
    additions={
      'usr/lib/libobjc.tbd':[s for s in symbols if s.startswith(('_objc_','_class_','_sel_','_protocol_'))],
      # verify_guest_imports.py checks every one of these against the exact
      # guest cache exports, so a stub entry cannot invent a missing symbol.
      'usr/lib/libSystem.tbd':['_getprogname','_fflush','_stat','_time','_kill',
        '_pthread_create','_pthread_attr_init','_pthread_attr_setdetachstate','_pthread_attr_destroy',
        '_pthread_threadid_np'],
      'System/Library/Frameworks/Foundation.framework/Foundation.tbd':['_OBJC_CLASS_$_NSURL','_OBJC_CLASS_$_NSMapTable','_OBJC_CLASS_$_NSFileManager','_OBJC_CLASS_$_NSMethodSignature','_OBJC_CLASS_$_NSBundle'],
      'System/Library/Frameworks/IOSurface.framework/IOSurface.tbd':[s for s in symbols if s.startswith(('_IOSurface','_kIOSurface'))],
    }
    for rel,names in additions.items():
        f=a.out/'stubs'/rel;t=f.read_text();names=[s for s in names if '"'+s+'"' not in t]
        f.write_text(t.replace('symbols: [ ','symbols: [ '+''.join('"'+s+'", ' for s in names)))
    names=sorted(s for s in symbols if s.startswith('_IO') and not s.startswith('_IOSurface'))
    f=a.out/'stubs/System/Library/Frameworks/IOKit.framework/IOKit.tbd';f.parent.mkdir(exist_ok=True)
    f.write_text('--- !tapi-tbd\ntbd-version: 4\ntargets: [ arm64e-ios ]\ninstall-name: /System/Library/Frameworks/IOKit.framework/IOKit\nexports:\n  - targets: [ arm64e-ios ]\n    symbols: [ '+', '.join('"'+s+'"' for s in names)+' ]\n...\n')
    bundle=a.out/'DVMMetal.bundle';bundle.mkdir();binary=bundle/'DVMMetal'
    install='/System/Library/Extensions/DVMMetal.bundle/DVMMetal'
    run(['xcrun','clang',*flags,'-dynamiclib','-Wl,-install_name,'+install,str(obj),'-F',str(a.out/'stubs/System/Library/Frameworks'),'-L',str(a.out/'stubs/usr/lib'),
         '-framework','Foundation','-framework','CoreFoundation','-framework','Metal','-framework','IOSurface','-framework','IOKit','-lobjc','-o',str(binary)])
    (bundle/'Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier='org.darwin-vm.system-metal',CFBundleExecutable='DVMMetal',CFBundlePackageType='BNDL',CFBundleVersion='1')))
    # Add only a dependency command in verified zero header padding. Do not
    # shift text, chained fixups, entry points, or any existing load command.
    def add_dependency(executable):
        # Add only a dependency command in verified zero header padding. Do not
        # shift text, chained fixups, entry points, or any existing load command.
        b=bytearray(executable.read_bytes());original=bytes(b)
        if struct.unpack_from('<III',b)!= (0xfeedfacf,0x100000c,0x80000002):raise ValueError('requires an exact arm64e executable: '+str(executable))
        n,size=struct.unpack_from('<II',b,16);off=32
        for _ in range(n):
            cmd,length=struct.unpack_from('<II',b,off)
            if cmd in (0xc,0x80000018) and install.encode() in b[off:off+length]:raise ValueError('already bootstrapped')
            off+=length
        if off!=32+size:raise ValueError('command extent')
        name=install.encode()+b'\0';length=(24+len(name)+7)&~7
        payload=struct.pack('<6I',0xc,length,24,0,0x10000,0x10000)+name
        payload=payload.ljust(length,b'\0')
        if any(b[off:off+length]) or len(b[off:off+length])!=length:raise ValueError('nonempty header padding')
        b[off:off+length]=payload;struct.pack_into('<II',b,16,n+1,size+length)
        return original,bytes(b),dict(offset=off,bytes=length)
    original,patched,header_edit=add_dependency(a.backboardd)
    off,length=header_edit['offset'],header_edit['bytes']
    before=a.out/'backboardd.before';before.write_bytes(original)
    target=a.out/'backboardd';target.write_bytes(patched);target.chmod(0o755)
    poster_target=None;poster_edit=None
    if a.poster:
        poster_original,poster_patched,poster_edit=add_dependency(a.poster)
        (a.out/'MercuryPosterExtension.before').write_bytes(poster_original)
        poster_target=a.out/'MercuryPosterExtension';poster_target.write_bytes(poster_patched);poster_target.chmod(0o755)
        if instruction_sections(poster_original)!=instruction_sections(poster_patched):raise ValueError('poster instruction sections changed')
    result=run(['codesign','-d','--entitlements',':-',str(a.backboardd)],capture_output=True)
    entitlements=plistlib.loads(result.stdout);entitlements['platform-application']=True;entitlements['org.darwin-vm.transport']=True
    if a.runtime_probe or a.session_reload:
        entitlements.update({'org.darwin-vm.development-loader':True,'get-task-allow':True,'com.apple.private.oop-jit.loader':'previews'})
        entitlements['com.apple.security.exception.files.absolute-path.read-write']=['/private/var/tmp/dvm-gpu-runner/']
    key='com.apple.security.exception.iokit-user-client-class'
    entitlements[key]=list(dict.fromkeys(entitlements.get(key,[])+['IOKitDiagnosticsClient']))
    ep=a.out/'backboardd.entitlements.plist';ep.write_bytes(plistlib.dumps(entitlements))
    hashes=[]
    signing=[(bundle,[]),(target,['--entitlements',str(ep)])]
    if poster_target:
        # The extension keeps its own entitlements and gains the transport
        # entitlement and the user-client class exception, like backboardd.
        pe=plistlib.loads(run(['codesign','-d','--entitlements',':-',str(a.poster)],capture_output=True).stdout)
        pe['platform-application']=True;pe['org.darwin-vm.transport']=True
        pe[key]=list(dict.fromkeys(pe.get(key,[])+['IOKitDiagnosticsClient']))
        pep=a.out/'MercuryPosterExtension.entitlements.plist';pep.write_bytes(plistlib.dumps(pe))
        # Signed outside its bundle, codesign would derive a filename identifier;
        # ExtensionKit's launch constraint wants the bundle's own (POSTER1: AMFI
        # "Launch Constraint Violation ... Constraint not matched").
        original_sig=run(['codesign','-d','-vvvv',str(a.poster)],capture_output=True,text=True).stderr
        identifier=re.search(r'^Identifier=(\S+)$',original_sig,re.M).group(1)
        signing.append((poster_target,['--entitlements',str(pep),'-i',identifier]))
    for path,extra in signing:
        run(['codesign','--force','--sign','-','--timestamp=none',*extra,str(path)])
        run(['codesign','--verify','--strict',str(path)])
        sig=run(['codesign','-d','-vvv',str(path)],capture_output=True,text=True).stderr
        (a.out/(path.name+'.codesign.txt')).write_text(sig)
        hashes+=re.findall(r'^CDHash=(\w+)$',sig,re.M)
    sections=instruction_sections(original)
    if not sections or sections!=instruction_sections(target.read_bytes()):raise ValueError('backboardd instruction sections changed')
    (a.out/'instruction-preservation.json').write_text(json.dumps(dict(original_instruction_sections_unchanged=True,sections=sections),indent=2)+'\n')
    (a.out/'hashes.txt').write_text('\n'.join(hashes)+'\n')
    run(['python3',str(repo/'build_tc.py'),str(a.out/'hashes.txt'),str(a.out/'helper.tc')])
    nm=a.out/'system_bootstrap.nm-u';nm.write_bytes(subprocess.check_output(['nm','-u',str(binary)]))
    run(['python3',str(source/'verify_guest_imports.py'),'--output',str(a.out/'imports.tsv'),str(nm)])
    # Record dynamically resolved registration exports too.
    dynamic=a.out/'registration.nm-u';dynamic.write_text('_MTLAddDevice\n_MTLCreateSystemDefaultDevice\n')
    run(['python3',str(source/'verify_guest_imports.py'),'--output',str(a.out/'registration-exports.tsv'),str(dynamic)])
    deps=subprocess.check_output(['xcrun','clang',*flags,'-MM',str(source/'system_bootstrap.m')],text=True)
    (a.out/'dependencies.txt').write_text(deps)
    for f in source.iterdir():
        if f.suffix in ('.m','.h','.inc','.py'):shutil.copyfile(f,a.out/f.name)
    (a.out/'build.json').write_text(json.dumps(dict(commands=commands,seconds=time.monotonic()-start,
        runtime_probe=a.runtime_probe,session_reload=a.session_reload,surface_pin_probe=a.surface_pin_probe,surface_import=a.surface_import,
        before_sha256=sha(before),after_sha256=sha(target),plugin_sha256=sha(binary),
        dependency=install,header_edit=dict(offset=off,bytes=length),
        poster=str(a.poster) if a.poster else None,poster_header_edit=poster_edit,
        poster_before_sha256=sha(a.out/'MercuryPosterExtension.before') if poster_target else None,
        poster_after_sha256=sha(poster_target) if poster_target else None,
        scope='backboardd boot dependency and dedicated transport entitlement; no kernel, guest cache, SPTM or TXM edits'),indent=2)+'\n')
    print(a.out)
if __name__=='__main__':main()
