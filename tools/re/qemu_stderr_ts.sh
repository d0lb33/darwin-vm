#!/bin/bash
# QEMU_WRAPPER for tools/probe.sh: run QEMU with its stderr piped through
# tools/re/ts_filter.py so every device-model trace line carries a host
# timestamp (seconds since launch). probe.sh redirects our stderr to the
# usual <tag>.stderr.log, so nothing else changes.
#
#   QEMU_WRAPPER=tools/re/qemu_stderr_ts.sh tools/re/manifest_probe.sh ...
exec "$@" 2> >(exec python3 -u "$(dirname "${BASH_SOURCE[0]}")/ts_filter.py" >&2)
