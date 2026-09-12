#!/usr/bin/env python3
"""Restore the reviewed native input helper on an isolated GPU baseline child.

Only the copied restore ramdisk is host-mounted. Every preimage is checked
inside the installer guest before changing the input helper; launchd is kept.
"""
import argparse, hashlib, json, shutil, subprocess
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--before',type=Path,nargs='+',required=True)
p.add_argument('--build',type=Path,required=True)
p.add_argument('--cache',type=Path,required=True)
p.add_argument('--current-tc',type=Path,required=True,
               help='trust cache for the exact source lineage; the input helper hash is merged into it')
p.add_argument('--out',type=Path,required=True)
a=p.parse_args();a.out.mkdir(exist_ok=False)
repo=Path(__file__).resolve().parents[2]
payload=a.out/'payload';payload.mkdir()
for source,name in [(source,f'input-before-{i}') for i,source in enumerate(a.before)]+[(a.build/'dvm-input','input-after'),(a.cache,'cache-before')]:
    shutil.copyfile(source,payload/name)
script=payload/'gpu-load-install.sh'
script.write_text('''#!/bin/sh
set -eu
mount_apfs /dev/disk1s1 /mnt1
actual=$(cksum < /mnt1/usr/local/libexec/dvm-input)
echo "GPU_INPUT_PREIMAGE actual=$actual"
matched=no
for candidate in /libexec/input-before-*; do
    if test "$actual" = "$(cksum < "$candidate")"; then
        echo "GPU_INPUT_PREIMAGE matched=$candidate"
        matched=yes
        break
    fi
done
test "$matched" = yes
test "$(cksum < /mnt1/System/Library/xpc/launchd.plist)" = "$(cksum < /libexec/cache-before)"
test ! -e /mnt1/usr/local/libexec/dvm-power-pv-service
echo GPU_INPUT_PREIMAGES_VERIFIED
cp /libexec/input-after /mnt1/usr/local/libexec/dvm-input.new
chmod 755 /mnt1/usr/local/libexec/dvm-input.new
chown 0:0 /mnt1/usr/local/libexec/dvm-input.new
test "$(cksum < /mnt1/usr/local/libexec/dvm-input.new)" = "$(cksum < /libexec/input-after)"
mv /mnt1/usr/local/libexec/dvm-input.new /mnt1/usr/local/libexec/dvm-input
sync
echo GPU_LOAD_INSTALLED
''')
subprocess.run(['bash','-n',str(script)],check=True)
image=a.out/'ramdisk.dmg';shutil.copyfile(repo/'firmware/ramdisk.dmg',image)
wrapper=repo/'tools/rootfs/safe_attach.sh'
mount=Path(subprocess.check_output([str(wrapper),'attach',str(image),'--owners','on'],text=True).strip())
try:
    for f in payload.iterdir():shutil.copyfile(f,mount/'libexec'/f.name)
    subprocess.run(['sync'],check=True)
finally:subprocess.run([str(wrapper),'detach',str(mount)],check=True)
subprocess.run(['python3',str(repo/'tools/rootfs/merge_tc.py'),str(a.out/'system.tc'),
                str(a.current_tc),str(a.build/'helper.tc')],check=True)
(a.out/'provenance.json').write_text(json.dumps(dict(
    scope='restore reviewed native HID on disposable reconstructed GPU child; keep launchd jobs, native SMC and driver',
    trust_cache='merge exact source-lineage cache with only the reviewed input helper CDHash',
    inputs={str(f.resolve()):hashlib.sha256(f.read_bytes()).hexdigest()
            for f in [*a.before,a.build/'dvm-input',a.build/'helper.tc',a.current_tc,a.cache,script]}),indent=2)+'\n')
