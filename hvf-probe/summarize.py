#!/usr/bin/env python3
"""Condense results.txt into one line per (mode, register) with a verdict class."""
import re, sys, collections

MODES, cur, rows = [], None, []
for line in open(sys.argv[1] if len(sys.argv) > 1 else 'results.txt'):
    m = re.match(r'=+ MODE: (.*?) =+', line.strip())
    if m:
        cur = m.group(1); MODES.append(cur); continue
    if cur is None: continue
    m = re.match(r'\s{2}(\S.*?)\s{2,}(S\d_\d_C\d+_C\d+_\d+|\s*)\s+(read|write.*?|read-back|exec|)\s{2,}(\S.*)$', line.rstrip())
    if m:
        rows.append((cur, m.group(1).strip(), m.group(2).strip(), m.group(3).strip(), m.group(4).strip()))

def verdict(res):
    if res.startswith('NATIVE'):        return 'native'
    if res.startswith('TRAP->VMM') or res.startswith('EXIT->VMM'): return 'trap-to-vmm'
    if 'EC=0x18' in res and 'GUEST-EXC' in res: return 'trap-to-guest-el2'
    if res.startswith('GUEST-EXC'):     return 'guest-exception'
    if res.startswith('CRASHED'):       return 'framework-abort'
    return 'no-result'

tally = collections.Counter()
for mode, label, enc, op, res in rows:
    tally[(mode, verdict(res))] += 1
    print(f"{mode}\t{label}\t{enc}\t{op}\t{verdict(res)}\t{res}")

print("\n=== per-mode tally ===", file=sys.stderr)
for mode in MODES:
    line = ", ".join(f"{v}={n}" for (m2, v), n in sorted(tally.items()) if m2 == mode)
    print(f"{mode}: {line}", file=sys.stderr)
