#!/bin/bash
# manifest_probe.sh - run tools/probe.sh with a warm-disk manifest's inputs,
# environment and a fresh qcow2 child, so gdbstub bring-up loops can use the
# system disk boot exactly as boot_native_smc.py would launch it.
#
# usage: tools/re/manifest_probe.sh MANIFEST TAG SECS [extra QEMU args...]
#   e.g. tools/re/manifest_probe.sh manifest.json SYS_BP1 120 -S -gdb tcp:127.0.0.1:4713
#
# The manifest's qemu_env DARWIN_* variables are exported, its bootkc/dtree/
# tc/ramdisk/sptm/txm/-m/-smp/-accel/-fb arguments are reused, and the ANS
# drive is a new child of the manifest disk under /tmp/dvm/TAG/. The QEMU
# binary is the manifest's argv[0] unless DVM_QEMU overrides it.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MANIFEST="$1"; TAG="$2"; SECS="$3"; shift 3
OUT="/tmp/dvm/$TAG"; mkdir -p "$OUT"
eval "$(python3 - "$MANIFEST" "$OUT" <<'EOF'
import json, shlex, sys
m = json.load(open(sys.argv[1])); out = sys.argv[2]
argv = m['qemu_argv']
def arg(k):
    return argv[argv.index(k) + 1]
print('QEMU_BIN=%s' % shlex.quote(argv[0]))
for k, v in m['qemu_env'].items():
    print('export %s=%s' % (k, shlex.quote(v)))
print('BOOTKC=%s' % shlex.quote(arg('-bootkc')))
print('DTREE=%s' % shlex.quote(arg('-dtree')))
print('TC=%s' % shlex.quote(arg('-tc')))
print('RAMDISK=%s' % shlex.quote(arg('-ramdisk')))
print('BOOTARGS=%s' % shlex.quote(arg('-args')))
print('MEM=%s' % shlex.quote(arg('-m')))
extra = []
for k in ('-smp', '-accel', '-fb', '-fbmode'):
    if k in argv:
        extra += [k, arg(k)]
print('EXTRA=%s' % shlex.quote(' '.join(extra)))
print('DISK=%s' % shlex.quote(m['disk']['path']))
EOF
)"
qemu-img create -q -f qcow2 -F qcow2 -b "$DISK" "$OUT/disk.qcow2"
export DVM_QEMU="${DVM_QEMU:-$QEMU_BIN}"
# shellcheck disable=SC2086
exec "$REPO/tools/probe.sh" --dtree "$DTREE" --bootkc "$BOOTKC" --tc "$TC" --ramdisk "$RAMDISK" \
    --bootargs "$BOOTARGS" --mem "$MEM" --secs "$SECS" --tag "$TAG" --out "$OUT" \
    -- $EXTRA -drive "if=none,id=ans,file=$OUT/disk.qcow2,format=qcow2" "$@"
