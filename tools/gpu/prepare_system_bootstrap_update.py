#!/usr/bin/env python3
"""Stage a guarded replacement of an installed DVMMetal boot carrier.

This is for a disposable child whose System volume already contains the
session-reload bootstrap.  It verifies the exact installed carrier and
backboardd before replacing either, preserves the previous carrier, and adds
the new CodeDirectory hashes to the existing trust cache.  It does not touch
Data, SPTM, TXM, the device tree, or unrelated launchd jobs.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

from build_system_bootstrap import sha


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("build", type=Path,
                   help="new build_system_bootstrap.py output")
    p.add_argument("old_backboardd", type=Path,
                   help="exact currently installed patched backboardd")
    p.add_argument("old_bundle", type=Path,
                   help="exact currently installed DVMMetal.bundle")
    p.add_argument("current_tc", type=Path,
                   help="trust cache used by the current boot manifest")
    p.add_argument("out", type=Path)
    a = p.parse_args()
    a.build = a.build.resolve()
    a.old_backboardd = a.old_backboardd.resolve()
    a.old_bundle = a.old_bundle.resolve()
    a.current_tc = a.current_tc.resolve()
    a.out = a.out.resolve()
    if not (a.build / "build.json").is_file():
        p.error("new build is missing build.json")
    for path in (a.old_backboardd, a.old_bundle / "DVMMetal", a.current_tc):
        if not path.is_file():
            p.error(f"missing input: {path}")
    a.out.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[2]
    old_plugin_sha = sha(a.old_bundle / "DVMMetal")
    # A carrier may be revised more than once along a disposable lineage. A
    # fixed `.dvm-previous` name safely allowed only the first update and made
    # later installers exit after their preimage checks. Preserve every prior
    # carrier under the digest that identified the guarded preimage.
    backup = "DVMMetal.bundle.dvm-previous-" + old_plugin_sha[:16]
    script = f"""#!/bin/sh
set -eu
mount_apfs /dev/disk1s1 /mnt1
test "$(cksum < /mnt1/usr/libexec/backboardd)" = "$(cksum < /libexec/backboardd.before)"
test "$(cksum < /mnt1/System/Library/Extensions/DVMMetal.bundle/DVMMetal)" = "$(cksum < /libexec/DVMMetal.before)"
test ! -e /mnt1/System/Library/Extensions/{backup}
mv /mnt1/System/Library/Extensions/DVMMetal.bundle /mnt1/System/Library/Extensions/{backup}
cp -R /libexec/DVMMetal.bundle /mnt1/System/Library/Extensions/DVMMetal.bundle
cp /libexec/backboardd /mnt1/usr/libexec/backboardd
chmod 755 /mnt1/usr/libexec/backboardd /mnt1/System/Library/Extensions/DVMMetal.bundle/DVMMetal
chown 0:0 /mnt1/usr/libexec/backboardd
chown -R 0:0 /mnt1/System/Library/Extensions/DVMMetal.bundle
test "$(cksum < /mnt1/usr/libexec/backboardd)" = "$(cksum < /libexec/backboardd)"
test "$(cksum < /mnt1/System/Library/Extensions/DVMMetal.bundle/DVMMetal)" = "$(cksum < /libexec/DVMMetal.bundle/DVMMetal)"
sync
echo GPU_LOAD_INSTALLED
"""
    (a.out / "gpu-load-install.sh").write_text(script)
    subprocess.run(["bash", "-n", str(a.out / "gpu-load-install.sh")],
                   check=True)
    image = a.out / "ramdisk.dmg"
    shutil.copyfile(repo / "firmware/ramdisk.dmg", image)
    wrapper = repo / "tools/rootfs/safe_attach.sh"
    mount = Path(subprocess.check_output(
        [str(wrapper), "attach", str(image), "--owners", "on"],
        text=True).strip())
    try:
        libexec = mount / "libexec"
        shutil.copyfile(a.old_backboardd, libexec / "backboardd.before")
        shutil.copyfile(a.old_bundle / "DVMMetal", libexec / "DVMMetal.before")
        shutil.copyfile(a.build / "backboardd", libexec / "backboardd")
        shutil.copytree(a.build / "DVMMetal.bundle", libexec / "DVMMetal.bundle")
        shutil.copyfile(a.out / "gpu-load-install.sh",
                        libexec / "gpu-load-install.sh")
        subprocess.run(["sync"], check=True)
    finally:
        subprocess.run([str(wrapper), "detach", str(mount)], check=True)
    subprocess.run([
        "python3", str(repo / "tools/rootfs/merge_tc.py"),
        str(a.out / "system.tc"), str(a.current_tc),
        str(a.build / "helper.tc")], check=True)
    (a.out / "provenance.json").write_text(json.dumps({
        "format": "darwin-vm-system-bootstrap-update-v1",
        "build": str(a.build),
        "build_sha256": sha(a.build / "build.json"),
        "old_backboardd": {"path": str(a.old_backboardd),
                             "sha256": sha(a.old_backboardd)},
        "old_plugin": {"path": str(a.old_bundle / "DVMMetal"),
                       "sha256": old_plugin_sha},
        "previous_carrier_path": "/System/Library/Extensions/" + backup,
        "new_backboardd_sha256": sha(a.build / "backboardd"),
        "new_plugin_sha256": sha(a.build / "DVMMetal.bundle/DVMMetal"),
        "current_tc": str(a.current_tc),
        "scope": "backboardd-only boot registration; guarded carrier replacement in one disposable child; previous carrier retained; launchd and Data unchanged",
    }, indent=2) + "\n")
    print(a.out)


if __name__ == "__main__":
    main()
