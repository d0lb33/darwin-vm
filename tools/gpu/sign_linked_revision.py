#!/usr/bin/env python3
"""Create a bounded ad-hoc OOP-JIT linkage signature for a disposable driver bundle.

CodeDirectory v0x20600 layout: XNU cs_blobs.h. Exact TXM 24A5430a checks:
0xfffffff017046028 (linkage fields), 0xfffffff0170467bc (application 2),
0xfffffff017047220 (subtype and current loader entitlement). Not a trust bypass
or proof that the guest accepts this file; host and guest validation are separate.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess


def commands(b):
    if b[:4] != bytes.fromhex('cffaedfe'):
        raise ValueError('requires thin little-endian Mach-O 64')
    o=32
    for _ in range(struct.unpack_from('<I',b,16)[0]):
        c,n=struct.unpack_from('<II',b,o)
        if n<8 or o+n>len(b):raise ValueError('load command extent')
        yield c,o
        o+=n


def signature(b):
    entries=[o for c,o in commands(b) if c==0x1d]
    if len(entries)!=1:raise ValueError('exactly one code signature required')
    lc=entries[0];off,n=struct.unpack_from('<II',b,lc+8)
    if off+n!=len(b):raise ValueError('signature must end file')
    sb=b[off:off+n];magic,size,count=struct.unpack_from('>III',sb)
    if magic!=0xfade0cc0 or size>n or count>16:raise ValueError('embedded signature extent')
    slots=[]
    for i in range(count):
        slot,p=struct.unpack_from('>II',sb,12+8*i);length=struct.unpack_from('>I',sb,p+4)[0]
        if length<8 or p+length>size:raise ValueError('signature slot extent')
        slots.append((slot,sb[p:p+length]))
    cds=[x for k,x in slots if k==0]
    if len(cds)!=1:raise ValueError('one primary CodeDirectory required')
    return lc,off,slots,cds[0]


def verify_pages(b,cd):
    ho,ident,special,n,limit=struct.unpack_from('>5I',cd,16)
    hs,ht,platform,ps=struct.unpack_from('>4B',cd,36)
    if hs!=32 or ht!=2 or n!=(limit+(1<<ps)-1)//(1<<ps):raise ValueError('SHA256 page layout')
    for i in range(n):
        h=hashlib.sha256(b[i*(1<<ps):min((i+1)*(1<<ps),limit)]).digest()
        if h!=cd[ho+32*i:ho+32*(i+1)]:raise ValueError('page hash mismatch '+str(i))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('bundle',type=Path);p.add_argument('out',type=Path)
    p.add_argument('--parent',type=Path,required=True,help='boot-trusted loader executable')
    p.add_argument('--subtype',type=int,choices=(1,2),default=1)
    a=p.parse_args();a.out.mkdir(exist_ok=False)
    dst=a.out/'DVMProxy.bundle';shutil.copytree(a.bundle,dst)
    binary=dst/'DVMProxy';old=binary.read_bytes();b=bytearray(old)
    lc,off,slots,cd=signature(old);verify_pages(old,cd)
    if struct.unpack_from('>I',cd,8)[0]!=0x20400 or struct.unpack_from('>I',cd,12)[0]!=2:
        raise ValueError('only the bounded v20400 ad-hoc fixture is supported')
    if any(k not in (0,2,0x10000) for k,_ in slots):raise ValueError('unexpected signature slot')
    if any(struct.unpack_from('>I',cd,o)[0] for o in (44,48,52)):
        raise ValueError('scatter/team/spare extension unsupported')
    parent=a.parent.read_bytes();_,_,_,parent_cd=signature(parent);verify_pages(parent,parent_cd)
    linkage=hashlib.sha256(parent_cd).digest()
    # Add the v20500 and v20600 fixed fields ahead of existing dynamic data.
    nc=bytearray(cd[:88]+bytes(20)+cd[88:])
    for o in (16,20):struct.pack_into('>I',nc,o,struct.unpack_from('>I',cd,o)[0]+20)
    struct.pack_into('>I',nc,8,0x20600)
    struct.pack_into('>BBHII',nc,96,2,2,a.subtype,len(nc),len(linkage))
    nc+=linkage;struct.pack_into('>I',nc,4,len(nc))
    length=12+8*len(slots)+sum(len(nc) if k==0 else len(v) for k,v in slots)
    allocated=(length+15)&~15
    struct.pack_into('<I',b,lc+12,allocated)
    # __LINKEDIT includes the signature; changed load commands are rehashed.
    for c,o in commands(b):
        if c==0x19 and bytes(b[o+8:o+24]).rstrip(b'\0')==b'__LINKEDIT':
            fileoff=struct.unpack_from('<Q',b,o+40)[0]
            newsize=off+allocated-fileoff
            if fileoff>off:raise ValueError('linkedit starts after signature')
            struct.pack_into('<Q',b,o+48,newsize)
            if struct.unpack_from('<Q',b,o+32)[0]<newsize:struct.pack_into('<Q',b,o+32,(newsize+16383)&~16383)
    ho,ident,special,n,limit=struct.unpack_from('>5I',nc,16);ps=nc[39]
    for i in range(n):nc[ho+32*i:ho+32*(i+1)]=hashlib.sha256(b[i*(1<<ps):min((i+1)*(1<<ps),limit)]).digest()
    body=bytearray();index=bytearray();cursor=12+8*len(slots)
    for k,v in slots:
        v=nc if k==0 else v;index+=struct.pack('>II',k,cursor);body+=v;cursor+=len(v)
    sb=struct.pack('>III',0xfade0cc0,length,len(slots))+index+body
    binary.write_bytes(b[:off]+sb+bytes(allocated-len(sb)));new=binary.read_bytes();verify_pages(new,nc)
    r=subprocess.run(['codesign','--verify','--strict','--verbose=4',str(dst)],text=True,capture_output=True)
    record=dict(source=str(a.bundle.resolve()),parent=str(a.parent.resolve()),
        source_sha256=hashlib.sha256(old).hexdigest(),sha256=hashlib.sha256(new).hexdigest(),
        code_directory_sha256=hashlib.sha256(nc).hexdigest(),parent_code_directory_sha256=linkage.hex(),
        version=hex(0x20600),application_type=2,application_subtype=a.subtype,
        page_hashes_verified=True,host_verify_exit=r.returncode,host_verify_stdout=r.stdout,
        host_verify_stderr=r.stderr,guest_accepted=False)
    (a.out/'linkage-signature.json').write_text(json.dumps(record,indent=2)+'\n')
    shutil.copy2(__file__,a.out/Path(__file__).name)
    print(json.dumps(record));r.check_returncode()


if __name__=='__main__':main()
