#!/usr/bin/env python3
"""Stage boot registration on a copied restore ramdisk for an isolated child."""
import argparse,json,plistlib,shutil,subprocess
from pathlib import Path
from build_system_bootstrap import sha
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('build',type=Path);p.add_argument('cache',type=Path);p.add_argument('tc',type=Path);p.add_argument('out',type=Path)
    a=p.parse_args();a.out.mkdir(exist_ok=False);repo=Path(__file__).resolve().parents[2]
    cache=plistlib.loads(a.cache.read_bytes());jobs=cache['LaunchDaemons']
    helper='/System/Library/LaunchDaemons/org.darwin-vm.gpu-load.plist'
    if jobs[helper]['ProgramArguments']!=['/usr/local/libexec/dvm-gpu-load']:raise ValueError('helper job changed')
    # One producer: leave the installed runner intact but disabled in this
    # boot image. Backboardd owns this VM's transport for the complete session.
    jobs[helper]['Disabled']=True;jobs[helper]['RunAtLoad']=False
    bb='/System/Library/LaunchDaemons/com.apple.backboardd.plist'
    if jobs[bb]['ProgramArguments']!=['/usr/libexec/backboardd']:raise ValueError('compositor job changed')
    # backboardd runs as mobile. Its spawn file actions must not require
    # opening the root console; the first console-redirection trial did not
    # create a backboardd process. This path is in its existing home directory.
    jobs[bb]['StandardOutputPath']='/private/var/mobile/dvm-system-metal.log'
    jobs[bb]['StandardErrorPath']='/private/var/mobile/dvm-system-metal.log'
    (a.out/'launchd.plist').write_bytes(plistlib.dumps(cache,fmt=plistlib.FMT_BINARY,sort_keys=False))
    script='''#!/bin/sh
set -eu
mount_apfs /dev/disk1s1 /mnt1
test "$(cksum < /mnt1/usr/libexec/backboardd)" = "$(cksum < /libexec/backboardd.before)"
test "$(cksum < /mnt1/System/Library/xpc/launchd.plist)" = "$(cksum < /libexec/cache.before)"
test ! -e /mnt1/System/Library/Extensions/DVMMetal.bundle
test ! -e /mnt1/usr/libexec/backboardd.dvm-software
cp /mnt1/usr/libexec/backboardd /mnt1/usr/libexec/backboardd.dvm-software
cp /mnt1/System/Library/xpc/launchd.plist /mnt1/System/Library/xpc/launchd.plist.dvm-software
cp -R /libexec/DVMMetal.bundle /mnt1/System/Library/Extensions/DVMMetal.bundle
cp /libexec/backboardd /mnt1/usr/libexec/backboardd
cp /libexec/cache.after /mnt1/System/Library/xpc/launchd.plist
chmod 755 /mnt1/usr/libexec/backboardd /mnt1/System/Library/Extensions/DVMMetal.bundle/DVMMetal
chown 0:0 /mnt1/usr/libexec/backboardd
chown -R 0:0 /mnt1/System/Library/Extensions/DVMMetal.bundle
test "$(cksum < /mnt1/usr/libexec/backboardd)" = "$(cksum < /libexec/backboardd)"
test "$(cksum < /mnt1/System/Library/Extensions/DVMMetal.bundle/DVMMetal)" = "$(cksum < /libexec/DVMMetal.bundle/DVMMetal)"
test "$(cksum < /mnt1/System/Library/xpc/launchd.plist)" = "$(cksum < /libexec/cache.after)"
sync
echo GPU_LOAD_INSTALLED
'''
    (a.out/'gpu-load-install.sh').write_text(script)
    subprocess.run(['bash','-n',str(a.out/'gpu-load-install.sh')],check=True)
    image=a.out/'ramdisk.dmg';shutil.copyfile(repo/'firmware/ramdisk.dmg',image)
    wrapper=repo/'tools/rootfs/safe_attach.sh'
    mount=Path(subprocess.check_output([str(wrapper),'attach',str(image),'--owners','on'],text=True).strip())
    try:
        d=mount/'libexec'
        for n in ('backboardd','backboardd.before'):shutil.copyfile(a.build/n,d/n)
        shutil.copytree(a.build/'DVMMetal.bundle',d/'DVMMetal.bundle')
        shutil.copyfile(a.cache,d/'cache.before');shutil.copyfile(a.out/'launchd.plist',d/'cache.after')
        shutil.copyfile(a.out/'gpu-load-install.sh',d/'gpu-load-install.sh')
        subprocess.run(['sync'],check=True)
    finally:subprocess.run([str(wrapper),'detach',str(mount)],check=True)
    subprocess.run(['python3',str(repo/'tools/rootfs/merge_tc.py'),str(a.out/'system.tc'),str(a.tc),str(a.build/'helper.tc')],check=True)
    (a.out/'provenance.json').write_text(json.dumps(dict(build=str(a.build.resolve()),build_sha256=sha(a.build/'build.json'),
        before_cache_sha256=sha(a.cache),after_cache_sha256=sha(a.out/'launchd.plist'),
        scope='backboardd-only boot registration; runner disabled; original software executable/cache saved in disposable child'),indent=2)+'\n')
if __name__=='__main__':main()
