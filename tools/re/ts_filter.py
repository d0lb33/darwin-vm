#!/usr/bin/env python3
"""Prefix every stdin line with seconds since this filter started.

Used by tools/re/qemu_stderr_ts.sh so device-model stderr traces carry host
time; the model's own fprintf() lines have none, which makes sleep/wake and
RPC cadence measurements impossible from a plain stderr log.
"""
import sys
import time

t0 = time.monotonic()
out = sys.stdout
for line in sys.stdin:
    out.write("%9.3f %s" % (time.monotonic() - t0, line))
    out.flush()
