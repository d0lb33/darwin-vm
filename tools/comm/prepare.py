#!/usr/bin/env python3
"""Stage an isolated service owner for CellularPlanDaemon; no shared-cache edits."""
import argparse,hashlib,json,plistlib,shutil,subprocess
from pathlib import Path
SERVICE='com.apple.CellularPlanDaemon.xpc'
def derive(cache):
    jobs=cache['LaunchDaemons'];owners=[]
    for path,job in jobs.items():
        if SERVICE in job.get('MachServices',{}):
            if job.get('ProgramArguments')!=['/System/Library/Frameworks/CoreTelephony.framework/Support/CommCenter']:
                raise ValueError('unexpected cellular endpoint owner')
            owners.append(path);del job['MachServices'][SERVICE]
    if owners!=['/System/Library/LaunchDaemons/com.apple.CommCenter.plist']:
        raise ValueError('expected the exact cellular CommCenter owner; noncellular variant does not publish this endpoint')
    name='/System/Library/LaunchDaemons/org.darwin-vm.cellular-plan.plist'
    if name in jobs:raise ValueError('already installed')
    jobs[name]=dict(Label='org.darwin-vm.cellular-plan',ProgramArguments=['/usr/local/libexec/dvm-cellular-plan'],
        MachServices={SERVICE:True},RunAtLoad=True,KeepAlive=True,ThrottleInterval=5,
        StandardOutputPath='/dev/console',StandardErrorPath='/dev/console')
    return owners
def main():
    p=argparse.ArgumentParser();p.add_argument('build',type=Path);p.add_argument('cache',type=Path);p.add_argument('tc',type=Path);p.add_argument('out',type=Path);a=p.parse_args()
    a.out.mkdir(exist_ok=False);repo=Path(__file__).resolve().parents[2]
    cache=plistlib.loads(a.cache.read_bytes());owners=derive(cache)
    (a.out/'launchd.plist').write_bytes(plistlib.dumps(cache,fmt=plistlib.FMT_BINARY,sort_keys=False))
    script='''#!/bin/sh
set -eu
mount_apfs /dev/disk1s1 /mnt1
test "$(cksum < /mnt1/System/Library/xpc/launchd.plist)" = "$(cksum < /libexec/cache.before)"
test ! -e /mnt1/usr/local/libexec/dvm-cellular-plan
cp /libexec/dvm-cellular-plan /mnt1/usr/local/libexec/dvm-cellular-plan
chmod 755 /mnt1/usr/local/libexec/dvm-cellular-plan
chown 0:0 /mnt1/usr/local/libexec/dvm-cellular-plan
test "$(cksum < /mnt1/usr/local/libexec/dvm-cellular-plan)" = "$(cksum < /libexec/dvm-cellular-plan)"
cp /libexec/cache.after /mnt1/System/Library/xpc/launchd.plist
test "$(cksum < /mnt1/System/Library/xpc/launchd.plist)" = "$(cksum < /libexec/cache.after)"
sync
echo GPU_LOAD_INSTALLED
'''
    (a.out/'gpu-load-install.sh').write_text(script)
    image=a.out/'ramdisk.dmg';shutil.copyfile(repo/'firmware/ramdisk.dmg',image)
    wrapper=repo/'tools/rootfs/safe_attach.sh';mount=Path(subprocess.check_output([str(wrapper),'attach',str(image),'--owners','on'],text=True).strip())
    try:
        for src,name in ((a.build/'dvm-cellular-plan','dvm-cellular-plan'),(a.cache,'cache.before'),(a.out/'launchd.plist','cache.after'),(a.out/'gpu-load-install.sh','gpu-load-install.sh')):shutil.copyfile(src,mount/'libexec'/name)
        subprocess.run(['sync'],check=True)
    finally:subprocess.run([str(wrapper),'detach',str(mount)],check=True)
    subprocess.run(['python3',str(repo/'tools/rootfs/merge_tc.py'),str(a.out/'system.tc'),str(a.tc),str(a.build/'service.tc')],check=True)
    (a.out/'provenance.json').write_text(json.dumps(dict(scope='system-wide cellular-plan endpoint only; other CommCenter services unchanged; no modem emulation',previous_owners=owners,build=str(a.build),binary_sha256=hashlib.sha256((a.build/'dvm-cellular-plan').read_bytes()).hexdigest()),indent=2))
if __name__=='__main__':main()
