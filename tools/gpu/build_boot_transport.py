#!/usr/bin/env python3
"""Build an isolated, exact-24A5430a DT + guarded kernel mapping shim."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dt_patch import Node, decode, encode
from aux_namespace_dt import properties

BASE=0xfffffff007004000
CAVE=0xfffffff00b34b480
CAVE_END=0xfffffff00b34c000
UC=0xfffffff00b1f1a10
MAP=0xfffffff00b281de0
BOOT_SHA='da1e254ab81e31adae87c049da295b582dabbd4ba46096fc58f3e5467fc6e02c'
RAM=0x4f0000000
SIZE=0x1000000
REGSIZE=0x4000


def sha(b):return hashlib.sha256(b).hexdigest()


def branch(source,target):
    delta=target-source
    if delta%4 or not -(1<<27)<=delta<(1<<27):raise ValueError('branch range')
    return struct.pack('<I',0x14000000|((delta//4)&0x3ffffff))


def extend_tree(data):
    root,end=decode(data,0)
    if any(data[end:]):raise ValueError('DT trailer')
    arm=root.child('arm-io')
    if not arm or arm.child('dvm-transport'):raise ValueError('DT parent/duplicate')
    ranges=list(struct.iter_unpack('<QQQ',arm.get('ranges')))
    if ranges[0]!=(0,0x210000000,0x2f0000000):raise ValueError('arm-io translation changed')
    relative=RAM-ranges[0][1]
    def overlaps(n):
        reg=n.get('reg')
        if reg and len(reg)%16==0:
            for lo,length in struct.iter_unpack('<QQ',reg):
                if length and lo<relative+SIZE+REGSIZE and lo+length>relative:
                    raise ValueError('existing DT reg overlaps candidate: '+str(n.name()))
        for c in n.children:overlaps(c)
    for c in arm.children:overlaps(c)
    node=Node()
    node.set('name',b'dvm-transport\0')
    node.set('device_type',b'dvm-transport\0')
    node.set('compatible',b'dvm,shm-transport-v1\0')
    node.set('reg',struct.pack('<QQQQ',relative,SIZE,relative+SIZE,REGSIZE))
    arm.children.append(node)
    output=encode(root)
    before,after=properties(data),properties(output)
    for key,(start,end,_) in before.items():
        a,b,_=after[key]
        if data[start:end]!=output[a:b]:raise ValueError('unrelated DT property changed: '+str(key))
    if len(after)!=len(before)+4:raise ValueError('unexpected DT change')
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bootkc',type=Path,required=True)
    p.add_argument('--dtree',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    original=a.bootkc.read_bytes()
    if sha(original)!=BOOT_SHA:raise ValueError('requires pinned native-SMC 24A5430a BootKC')
    dt=extend_tree(a.dtree.read_bytes())
    if any(original[CAVE-BASE:CAVE_END-BASE]):raise ValueError('executable padding not empty')
    prologue=bytes.fromhex('7f2303d5ffc301d1')
    unsupported=bytes.fromhex('5f2403d5e05880520000bc72c0035fd6')
    if original[UC-BASE:UC-BASE+8]!=prologue or original[MAP-BASE:MAP-BASE+16]!=unsupported:
        raise ValueError('entry guard mismatch')
    # Chained pointer diversity + target checks, from exact kernel vtables.
    checks=[(0x7e58dd0,0x460,0x82cd,UC),(0x7e27b08,0x590,0x02d4,MAP),
            (0x7e58dd0,0x20,0x2e4a,None),(0x7e58dd0,0x28,0x3a87,None),
            (0x7e58dd0,0xd0,0x1814,None),(0x7e58dd0,0xd8,0x122e,None),
            (0x7e58dd0,0x1e8,0x0c9c,None),
            (0x7e58dd0,0x370,0x93b7,None),(0x7e58dd0,0x3c8,0x6c99,None),
            (0x7e58dd0,0x3b8,0xabe0,None),(0x7e58dd0,0x2f0,0x5ec5,None)]
    for table,slot,diversity,target in checks:
        value,=struct.unpack_from('<Q',original,(0xfffffff000000000|table)+slot-BASE)
        if not value>>63 or (value>>32)&65535!=diversity or (target and BASE+(value&0xffffffff)!=target):
            raise ValueError('kernel ABI check failed')
    a.out.mkdir(exist_ok=False)
    src=Path(__file__).with_name('boot_transport_shim.cpp')
    shutil.copyfile(src,a.out/src.name)
    shutil.copyfile(__file__,a.out/Path(__file__).name)
    sdk=Path(subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip())
    command=['xcrun','clang++','-target','arm64e-apple-ios27.0','-isysroot',str(sdk),
        '-I',str(sdk/'System/Library/Frameworks/Kernel.framework/Headers'),
        '-DKERNEL','-mkernel','-fno-exceptions','-fno-rtti','-fno-stack-protector',
        '-fno-builtin','-std=c++17','-Os','-S',str(src),'-o',str(a.out/'shim.s')]
    subprocess.run(command,check=True)
    asm=(a.out/'shim.s').read_text()
    if '.ptrauth_kernel_abi_version 0' not in asm:raise ValueError('kernel PAC ABI missing')
    lines=[]
    for line in asm.splitlines():
        s=line.strip()
        if s.startswith(('.build_version','.ptrauth_kernel_abi_version','.subsections_via_symbols','.loh')):continue
        if s.startswith('.section'):
            if '__text,' in s:line='.text'
            elif '__cstring,' in s:line='.section .rodata,"a"'
            else:raise ValueError('unexpected compiler section: '+s)
        line=line.split(';')[0]
        line=re.sub(r'([A-Za-z_.$][\w.$]*)@PAGEOFF',r':lo12:\1',line).replace('@PAGE','')
        lines.append(line)
    # Original prologue has no PC-relative operands. Its continuation remains
    # relative, as do all calls/literals, so the guest's kernel slide is retained.
    lines+=['.text','.p2align 2','.global _dvm_original_uc','_dvm_original_uc:',
            '.inst 0xd503237f','.inst 0xd101c3ff','b _dvm_uc_continue']
    (a.out/'shim-elf.s').write_text('\n'.join(lines)+'\n')
    (a.out/'layout.ld').write_text(f'SECTIONS {{ . = {CAVE:#x}; .text : {{ *(.text) }} .rodata : {{ *(.rodata) }} /DISCARD/ : {{ *(.comment) *(.note*) }} }}\n_dvm_uc_continue = {UC+8:#x};\n')
    llvm=Path('/opt/homebrew/opt/llvm/bin')
    subprocess.run(['xcrun','clang','-target','aarch64-none-elf','-march=armv8.3-a','-c',str(a.out/'shim-elf.s'),'-o',str(a.out/'shim.o')],check=True)
    linker=shutil.which('ld.lld')
    if not linker:raise ValueError('LLVM ld.lld is required')
    subprocess.run([linker,'-T',str(a.out/'layout.ld'),'-e','_dvm_uc',str(a.out/'shim.o'),'-o',str(a.out/'shim.elf')],check=True)
    subprocess.run([str(llvm/'llvm-objcopy'),'-O','binary',str(a.out/'shim.elf'),str(a.out/'shim.bin')],check=True)
    symbols=subprocess.check_output([str(llvm/'llvm-nm'),'--defined-only','--numeric-sort',str(a.out/'shim.elf')],text=True)
    (a.out/'symbols.txt').write_text(symbols)
    sym={parts[2]:int(parts[0],16) for line in symbols.splitlines() if len(parts:=line.split())==3}
    payload=(a.out/'shim.bin').read_bytes()
    if sym['_dvm_uc']!=CAVE or len(payload)>CAVE_END-CAVE:raise ValueError('payload does not fit padding')
    patched=bytearray(original)
    entries=[]
    for address,name in ((UC,'_dvm_uc'),(MAP,'_dvm_map')):
        value=struct.pack('<I',0xd503245f)+branch(address+4,sym[name])
        entries.append(dict(address=hex(address),before=original[address-BASE:address-BASE+8].hex(),after=value.hex()))
        patched[address-BASE:address-BASE+8]=value
    patched[CAVE-BASE:CAVE-BASE+len(payload)]=payload
    # Make the existing RX segment's file-backed extent include its padding.
    # The segment VM range, code pointers and every section stay in place.
    off=32;kernel=None
    for _ in range(struct.unpack_from('<I',original,16)[0]):
        cmd,n=struct.unpack_from('<II',original,off)
        if cmd==0x80000035:
            fileoff,nameoff=struct.unpack_from('<QI',original,off+16)
            name=original[off+nameoff:original.index(b'\0',off+nameoff)]
            if name==b'com.apple.kernel':kernel=fileoff
        off+=n
    if kernel is None:raise ValueError('kernel fileset missing')
    off=kernel+32;changed_extent=False
    for _ in range(struct.unpack_from('<I',original,kernel+16)[0]):
        cmd,n=struct.unpack_from('<II',original,off)
        if cmd==0x19 and original[off+8:off+24].rstrip(b'\0')==b'__TEXT_EXEC':
            va,vm,fo,fs=struct.unpack_from('<QQQQ',original,off+24)
            if va+fs!=0xfffffff00b34b458 or va+vm!=CAVE_END or fo!=va-BASE:raise ValueError('RX extent guard')
            new_fs=CAVE+len(payload)-va
            struct.pack_into('<Q',patched,off+48,new_fs)
            entries.append(dict(address=hex(BASE+off+48),before=struct.pack('<Q',fs).hex(),after=struct.pack('<Q',new_fs).hex(),purpose='RX file extent'))
            changed_extent=True
        off+=n
    if not changed_extent:raise ValueError('RX segment missing')
    (a.out/'bootkc').write_bytes(patched)
    (a.out/'system.dtree').write_bytes(dt)
    ledger=dict(scope='owned DT memory + gated stock IOKit allocation/mapping; no runtime kext',
        source_bootkc=str(a.bootkc.resolve()),source_bootkc_sha256=BOOT_SHA,
        output_bootkc_sha256=sha(patched),source_dtree_sha256=sha(a.dtree.read_bytes()),output_dtree_sha256=sha(dt),
        shim_source_sha256=sha(src.read_bytes()),payload_sha256=sha(payload),payload_address=hex(CAVE),payload_bytes=len(payload),
        entry_patches=entries,compile_command=command,abi_checks=checks,ram_base=hex(RAM),ram_size=SIZE,regs_base=hex(RAM+SIZE),regs_size=REGSIZE,
        sptm_modified=False,txm_modified=False)
    (a.out/'ledger.json').write_text(json.dumps(ledger,indent=2)+'\n')
    print(json.dumps(ledger,indent=2))


if __name__=='__main__':main()
