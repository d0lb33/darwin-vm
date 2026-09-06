#!/usr/bin/env python3
"""Offline, exact-build evidence for the presentation wait gate; no guest access.

Requires Homebrew libcapstone. Uses its C API so no Python package is needed.
This identifies a possible error-return path, not a runtime branch trace.
"""
import argparse
import ctypes as c
import hashlib
import json
from pathlib import Path
import struct

BASE=0xfffffff007004000
SHA='ed3ef577af60140ebfca494cdd3ab52ec97f3bddbd5c07a9020263742630df23'
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--bootkc',type=Path,required=True)
p.add_argument('--out',type=Path,required=True)
p.add_argument('--capstone',default='/opt/homebrew/lib/libcapstone.dylib')
a=p.parse_args();data=a.bootkc.read_bytes()
if hashlib.sha256(data).hexdigest()!=SHA:raise ValueError('unexpected BootKC; rederive addresses')
table=0xfffffff00829a308
pointer=struct.unpack_from('<Q',data,table-BASE)[0]
if BASE+(pointer&0xffffffff)!=0xfffffff00a0e5d7c:raise ValueError('selector-6 dispatch changed')
class Insn(c.Structure):
    _fields_=[('id',c.c_uint),('address',c.c_uint64),('size',c.c_uint16),
              ('bytes',c.c_ubyte*24),('mnemonic',c.c_char*32),
              ('op_str',c.c_char*160),('detail',c.c_void_p)]
lib=c.CDLL(a.capstone);handle=c.c_size_t()
if lib.cs_open(1,0,c.byref(handle)):raise RuntimeError('capstone ARM64 initialization')
lib.cs_disasm.argtypes=[c.c_size_t,c.c_void_p,c.c_size_t,c.c_uint64,c.c_size_t,c.POINTER(c.POINTER(Insn))]
lib.cs_disasm.restype=c.c_size_t
lib.cs_free.argtypes=[c.POINTER(Insn),c.c_size_t]
ranges=[('cache-allocation',0xfffffff00a0b917c,0xfffffff00a0b91b0),
        ('selector-6',0xfffffff00a0e5d7c,0xfffffff00a0e5df8),
        ('wait-dispatch',0xfffffff00a0bbd6c,0xfffffff00a0bbe44),
        ('gated-preflight',0xfffffff00a0c6fcc,0xfffffff00a0c715c),
        ('status-observer',0xfffffff00a0bc734,0xfffffff00a0bc79c)]
a.out.mkdir(exist_ok=False);records=[]
for name,lo,hi in ranges:
    block=data[lo-BASE:hi-BASE];insns=c.POINTER(Insn)()
    count=lib.cs_disasm(handle,block,len(block),lo,0,c.byref(insns))
    lines=[f'0x{insns[i].address:x}: {bytes(insns[i].bytes[:insns[i].size]).hex()} {insns[i].mnemonic.decode()} {insns[i].op_str.decode()}' for i in range(count)]
    lib.cs_free(insns,count)
    (a.out/(name+'.disass')).write_text('\n'.join(lines)+'\n')
    records.append(dict(name=name,start=hex(lo),end=hex(hi),bytes_sha256=hashlib.sha256(block).hexdigest()))
lib.cs_close(c.byref(handle))
report=dict(bootkc=str(a.bootkc.resolve()),bootkc_sha256=SHA,ranges=records,
    dispatch=dict(table=hex(table),target='0xfffffff00a0e5d7c',scalar_inputs=3),
    scope='static evidence only; the observed IOReturn does not prove this branch ran',
    gate='For wait mode 0, preflight refreshes the observer at framebuffer+0x5e00 if present. framebuffer+0x8c bit 0 set AND uint32(framebuffer+0x58f4)==0 branches from 0xa0c7110 to 0xa0c7154, producing 0xe00002c2 | 0x21 = 0xe00002e3 before queue search.',
    unresolved='Identify the status-observer registration/source and whether it is absent, returns zero, or reports a genuine power transition; verify at runtime without overriding return values.')
(a.out/'contract.json').write_text(json.dumps(report,indent=2)+'\n')
print(a.out)
