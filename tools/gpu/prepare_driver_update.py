#!/usr/bin/env python3
"""Stage a guarded driver-only update on a disposable child; preserve launchd jobs."""
import argparse
import json
import plistlib
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import sha256

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--before-build',type=Path,required=True)
p.add_argument('--build',type=Path,required=True)
p.add_argument('--cache',type=Path,required=True)
p.add_argument('--system-tc',type=Path,required=True)
p.add_argument('--out',type=Path,required=True)
p.add_argument('--start-interval',type=int,choices=(180,),help='diagnostic: launch once from a 180-second launchd interval, not RunAtLoad')
a=p.parse_args();a.out.mkdir(exist_ok=False)
repo=Path(__file__).resolve().parents[2]
payload=a.out/'payload';payload.mkdir()
files=['dvm-gpu-load','DVMProxy.bundle/DVMProxy','DVMProxy.bundle/Info.plist']
lines=['#!/bin/sh','set -eu','mount_apfs /dev/disk1s1 /mnt1']
q=shlex.quote
for i,name in enumerate(files):
    shutil.copyfile(a.before_build/name,payload/f'before-{i}')
    shutil.copyfile(a.build/name,payload/f'after-{i}')
    target=q('/mnt1/usr/local/libexec/'+name)
    lines.append(f'test "$(cksum < {target})" = "$(cksum < /libexec/before-{i})"')
shutil.copyfile(a.cache,payload/'before-cache')
lines += ['test "$(cksum < /mnt1/System/Library/xpc/launchd.plist)" = "$(cksum < /libexec/before-cache)"',
          'test ! -e /mnt1/usr/local/libexec/dvm-power-pv-service',
          'echo GPU_LOAD_UPDATE_PREIMAGES_VERIFIED']
if a.start_interval:
    cache=plistlib.loads(a.cache.read_bytes())
    job=cache['LaunchDaemons']['/System/Library/LaunchDaemons/org.darwin-vm.gpu-load.plist']
    if job.get('ProgramArguments') != ['/usr/local/libexec/dvm-gpu-load'] or not job.get('LaunchOnlyOnce'):
        raise ValueError('unexpected driver job')
    job['RunAtLoad']=False
    job['StartInterval']=a.start_interval
    encoded=plistlib.dumps(cache,fmt=plistlib.FMT_BINARY,sort_keys=False)
    (payload/'after-cache').write_bytes(encoded)
    (a.out/'launchd.plist').write_bytes(encoded)
    lines += ['cp /libexec/after-cache /mnt1/System/Library/xpc/launchd.plist',
              'test "$(cksum < /mnt1/System/Library/xpc/launchd.plist)" = "$(cksum < /libexec/after-cache)"']
for i,name in enumerate(files):
    target=q('/mnt1/usr/local/libexec/'+name)
    mode=644 if name.endswith('.plist') else 755
    lines += [f'cp /libexec/after-{i} {target}',f'chmod {mode} {target}',f'chown 0:0 {target}',
              f'test "$(cksum < {target})" = "$(cksum < /libexec/after-{i})"']
lines += ['sync','echo GPU_LOAD_INSTALLED']
script=payload/'gpu-load-install.sh';script.write_text('\n'.join(lines)+'\n')
subprocess.run(['bash','-n',str(script)],check=True)
image=a.out/'ramdisk.dmg';shutil.copyfile(repo/'firmware/ramdisk.dmg',image)
wrapper=repo/'tools/rootfs/safe_attach.sh'
mount=Path(subprocess.check_output([str(wrapper),'attach',str(image),'--owners','on'],text=True).strip())
try:
    for f in payload.iterdir():shutil.copyfile(f,mount/'libexec'/f.name)
    subprocess.run(['sync'],check=True)
finally:subprocess.run([str(wrapper),'detach',str(mount)],check=True)
subprocess.run([sys.executable,str(repo/'tools/rootfs/merge_tc.py'),str(a.out/'system.tc'),str(a.system_tc),str(a.build/'helper.tc')],check=True)
inputs={str(f.resolve()):sha256(f) for f in a.build.rglob('*') if f.is_file() and
        (f.suffix in ('.m','.h','.sh','.plist') or f.name in ('dvm-gpu-load','DVMProxy','helper.tc'))}
(a.out/'provenance.json').write_text(json.dumps(dict(build=str(a.build.resolve()),inputs=inputs,
    before_build=str(a.before_build.resolve()),cache_sha256=sha256(a.cache),
    start_interval=a.start_interval,
    scope='driver-only update; all preimages checked before writes; optional driver launch interval'),indent=2)+'\n')
print(a.out)
