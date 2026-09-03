#!/bin/bash
# stamp_growth.sh - record (host time, line count) of a growing log once a second,
# so an untimestamped log (QEMU stderr, the serial log) can be placed on the
# same timeline as lldb hits.  usage: stamp_growth.sh <file> <out> [secs]
f=$1; out=$2; secs=${3:-400}; end=$((SECONDS+secs))
: > "$out"
while (( SECONDS < end )); do
    printf '%s %s\n' "$(date +%s.%N | cut -c1-14)" "$(wc -l < "$f" 2>/dev/null || echo 0)" >> "$out"
    perl -e 'sleep 1'
done
