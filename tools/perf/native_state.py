#!/usr/bin/env python3
"""Capture a frozen native probe's GDB registers, including guest EL2 faults."""
import argparse
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
from arm_island_bench import Remote

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--port', type=int, required=True)
ap.add_argument('--output', type=Path, required=True)
ap.add_argument('--memory', type=lambda s: int(s, 0), action='append', default=[])
a = ap.parse_args()
r = Remote(a.port)
r.sock.settimeout(5)
try:
    r.command('?')
    raw = bytes.fromhex(r.command('g'))
    values = struct.unpack_from('<33Q', raw)
    report = {f'x{i}' if i < 31 else 'sp' if i == 31 else 'pc': hex(v)
              for i, v in enumerate(values)}
    report['pstate'] = hex(struct.unpack_from('<I', raw, 33 * 8)[0])
    xml = ''
    while True:
        chunk = r.command(f'qXfer:features:read:system-registers.xml:{len(xml):x},1000')
        xml += chunk[1:]
        if chunk[0] == 'l':
            break
    wanted = ('ESR_', 'ELR_', 'FAR_', 'SPSR_', 'VBAR_', 'TCR_', 'TTBR', 'SCTLR_',
              'HCR_', 'CPTR_', 'CPACR_', 'MDCR_', 'ID_AA64MMFR0')
    for reg in ET.fromstring(xml).iter('reg'):
        name = reg.attrib['name']
        if name.startswith(wanted):
            value = r.command(f'p{int(reg.attrib["regnum"]):x}')
            report[name] = hex(int.from_bytes(bytes.fromhex(value), 'little'))
    for key in ('pc', 'ELR_EL2'):
        addr = int(report.get(key, '0'), 16)
        report[key + '_code'] = r.command(f'm{addr:x},40')
    for addr in a.memory:
        report[f'memory_{addr:x}'] = r.command(f'm{addr:x},80')
    a.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
finally:
    r.sock.close()
