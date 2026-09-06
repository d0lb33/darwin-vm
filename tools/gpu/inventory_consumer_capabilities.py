#!/usr/bin/env python3
"""Inventory exact-cache context capability call sites against the shared profile."""
import argparse,hashlib,json,re,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'re'))
from cache_objc_calls import Cache

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('cache_directory',type=Path);p.add_argument('disassembly',type=Path);p.add_argument('out',type=Path)
a=p.parse_args();cache=Cache(a.cache_directory)
header=Path(__file__).with_name('driver_capabilities.h').read_text()
queries={m[1]:dict(type='BOOL' if m[0]=='B' else 'NSUInteger',definition=m[2],exact_call_sites=[]) for m in re.findall(r'^\s*([BU])\((\w+),([^)]*)\)',header,re.M)}
other={}
for line in a.disassembly.read_text().splitlines():
    m=re.search(r'^(0x[0-9a-f]+):.*\b(?:bl|b)\s+(0x[0-9a-f]+)',line)
    if not m:continue
    try:selector=cache.selector(int(m[2],16))
    except (ValueError,UnicodeError):continue
    witness=dict(pc=m[1],stub=m[2])
    if selector in queries:queries[selector]['exact_call_sites'].append(witness)
    else:other.setdefault(selector,[]).append(witness)
report=dict(scope='exact guest static call sites; branch reachability still requires runtime evidence',
    disassembly_sha256=hashlib.sha256(a.disassembly.read_bytes()).hexdigest(),
    profile_header_sha256=hashlib.sha256(header.encode()).hexdigest(),queries=queries,
    other_selectors=other,
    excluded_capability_receivers={'supportsDestination:':'MTLCaptureManager, not MTLDevice'},
    parameterized_device_queries={'supportsFamily:':'all full-family contracts unsupported'},
    host_rehearsal_only_queries=['isFramebufferReadSupported','vendorName'])
a.out.write_text(json.dumps(report,indent=2)+'\n')
print(f'{len(queries)} scalar queries; {sum(bool(x["exact_call_sites"]) for x in queries.values())} present in exact context constructor')
