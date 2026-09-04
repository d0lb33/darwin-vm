#!/usr/bin/env bash
# Optimize the portable TCG build while retaining assertions and debug symbols.
# Own build directory by default; do not use it during a boot or measurement.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QEMU_SRC="$REPO/qemu-sptm"
BUILD_DIR="${BUILD_DIR:-$QEMU_SRC/build-fast}"
JOBS="${JOBS:-12}"
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || { echo 'JOBS must be positive' >&2; exit 2; }

# Refuse concurrent use of this worktree's executables, including another build
# directory. A full source rebuild during a benchmark also contaminates timing.
if ps -axo command= | python3 -c '
import sys
root = sys.argv[1] + "/"
sys.exit(0 if any(line.lstrip().startswith(root) and "qemu-system-" in line
                 for line in sys.stdin) else 1)
' "$QEMU_SRC"; then
    echo "Stop this worktree's QEMU probes before building." >&2
    exit 1
fi

mkdir -p "$BUILD_DIR"
if [[ ! -f "$BUILD_DIR/config-host.mak" ]]; then
    configure_args=(--target-list=aarch64-softmmu)
    [[ "$(uname -s)" != Darwin ]] || configure_args+=(--disable-pvg)
    (cd "$BUILD_DIR" && "$QEMU_SRC/configure" "${configure_args[@]}")
fi
MESON="$BUILD_DIR/pyvenv/bin/meson"
"$MESON" configure -Doptimization=3 -Db_lto=true -Db_ndebug=false -Ddebug=true "$BUILD_DIR"
make -C "$BUILD_DIR" -j"$JOBS" qemu-system-aarch64
echo "Fast QEMU with assertions: $BUILD_DIR/qemu-system-aarch64"
