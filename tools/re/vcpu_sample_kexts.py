#!/usr/bin/env python3
"""Attribute idle_host_profile.py vCPU PC samples to kexts and user regions.

Reads one or more ``vcpu-samples.txt`` files (lines: ``t cpuN ELx 0xPC class``),
maps kernel PCs through tools/re/kc_text_map.py (project slide 0x20000000) and
prints a histogram per file.  Samples land on translation-block boundaries, so
exception vectors and post-WFI idle addresses are over-represented; treat the
numbers as indicative, not as a cycle profile.

    tools/re/vcpu_sample_kexts.py /tmp/dvm/idleprof/<TAG>/vcpu-samples.txt ...
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kc_text_map  # noqa: E402

IDLE_PC = 0xfffffff02aa654c8  # com.apple.kernel+0x54c8, the post-WFI idle return


def main():
    ranges = kc_text_map.load_map(os.environ.get("BOOTKC", "firmware/bootkc"))
    for path in sys.argv[1:]:
        hist = Counter()
        n = 0
        for line in open(path):
            parts = line.split()
            if len(parts) < 5 or parts[1] == "error":
                continue
            pc = int(parts[3], 16)
            n += 1
            if pc == IDLE_PC:
                hist["idle (WFI return)"] += 1
            elif pc >= 0xfffffff000000000:
                unslid = pc - kc_text_map.SLIDE
                name = next((nm for lo, hi, nm in ranges if lo <= unslid < hi), None)
                if name is None:
                    name = "kernel-space, outside kernelcache text (SPTM/TXM/vectors)"
                hist[name] += 1
            else:
                hist["user " + parts[4]] += 1
        print("%s: %d samples" % (path, n))
        for name, k in hist.most_common(15):
            print("   %6.2f%%  %s" % (100 * k / n, name))


if __name__ == "__main__":
    main()
