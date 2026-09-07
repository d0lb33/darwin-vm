#!/usr/bin/env python3
"""Guarded caller-memory pin probe added to the isolated runtime-loader BootKC."""
import argparse,hashlib,json,re,shutil,struct,subprocess
from pathlib import Path
from build_boot_transport import branch
from build_development_loader import commands
BASE=0xfffffff007004000
SHA='934efdcc2c084515d952fd07c3ff4cba463990da9f54d4be25878b7f2c8d00c9'
CAVE=0xfffffff00aa5e000
END=0xfffffff00aa60000
ENTRY=0xfffffff00b291bd0
LINKS={'_dvm_alloc_class':0xfffffff00b175ad8,'_dvm_current_proc':0xfffffff00b1720e8,
       '_dvm_entitled':0xfffffff0091ac43c,'_dvm_thread_task':0xfffffff00ab70c54,
       '_dvm_dispatch_continue':ENTRY+8,'_dvm_dispatch_table_page':0xfffffff007e28000}
GUARDS={ENTRY:'5f2403d5a35cfef063c0059104018052e50300aa060080d212fdff17',
        0xfffffff00b175ad8:'7f2303d5f44fbea9fd7b01a9fd430091',
        0xfffffff00b1720e8:'7f2303d5fd7bbfa9fd030091acf8e797',
        0xfffffff0091ac43c:'7f2303d5f657bda9f44f01a9fd7b02a9',
        0xfffffff00b2582a0:'7f2303d5ff8302d1fc6f04a9fa6705a9f85f06a9f65707a9',
        0xfffffff00ab70c54:'7f2303d5fd7bbfa9fd030091010c42f9',
        0xfffffff00ab70ccc:'201440f9fd7bc1a8ff0f5fd6'}
CHECKS=[(0x7e27b08,0x5d0,0x5d9d,ENTRY),
        (0x7e1f870,0x78,0xfe46,0xfffffff00b2582a0),
        (0x7e1f870,0x98,0x649a,0xfffffff00b256e60),
        (0x7e1f870,0xd8,0xf5b3,0xfffffff00b255f8c),
        (0x7e1f870,0xe0,0x9285,0xfffffff00b25563c)]
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('bootkc',type=Path);p.add_argument('out',type=Path);a=p.parse_args()
    b=a.bootkc.read_bytes()
    if hashlib.sha256(b).hexdigest()!=SHA:raise ValueError('requires pinned exact guest runtime-loader BootKC')
    for va,h in GUARDS.items():
        if b[va-BASE:va-BASE+len(bytes.fromhex(h))]!=bytes.fromhex(h):raise ValueError(f'entry guard {va:x}')
    for table,slot,div,target in CHECKS:
        v,=struct.unpack_from('<Q',b,(0xfffffff000000000|table)+slot-BASE)
        if not v>>63 or ((v>>32)&65535)!=div or BASE+(v&0xffffffff)!=target:raise ValueError('virtual ABI')
    if any(b[CAVE-BASE:END-BASE]):raise ValueError('padding not empty')
    outer=False;preceding=None
    for c,o in commands(b,0):
        if c==0x19:
            va,vm,fo,fs,mp,ip=struct.unpack_from('<QQQQii',b,o+24)
            if ip&4 and va<=CAVE and END<=va+fs and fo==va-BASE:outer=True
        if c!=0x80000035:continue
        h,no=struct.unpack_from('<QI',b,o+16);name=b[o+no:b.index(0,o+no)].decode()
        for sc,so in commands(b,h):
            if sc!=0x19:continue
            va,vm,fo,fs,mp,ip=struct.unpack_from('<QQQQii',b,so+24)
            if vm and va<END and CAVE<va+vm:raise ValueError('overlaps '+name)
            if name=='com.apple.filesystems.tmpfs' and ip&4 and va<0xfffffff00aa5c180<va+fs<=CAVE:
                if vm!=fs or fo!=va-BASE:raise ValueError('preceding extent')
                preceding=(so,va,fs)
    if not outer or preceding is None:raise ValueError('unowned RX padding')
    a.out.mkdir(exist_ok=False);src=Path(__file__).with_name('surface_pin_shim.cpp')
    shutil.copyfile(src,a.out/src.name);shutil.copyfile(__file__,a.out/Path(__file__).name)
    sdk=subprocess.check_output(['xcrun','--show-sdk-path'],text=True).strip()
    cmd=['xcrun','clang++','-target','arm64e-apple-ios27.0','-isysroot',sdk,'-I',sdk+'/System/Library/Frameworks/Kernel.framework/Headers',
         '-DKERNEL','-mkernel','-fno-exceptions','-fno-rtti','-fno-stack-protector','-fno-builtin','-std=c++17','-Os','-S',str(src),'-o',str(a.out/'shim.s')]
    subprocess.run(cmd,check=True);asm=(a.out/'shim.s').read_text()
    if '.ptrauth_kernel_abi_version 0' not in asm:raise ValueError('kernel PAC ABI')
    lines=[]
    for line in asm.splitlines():
        s=line.strip()
        if s.startswith(('.build_version','.ptrauth_kernel_abi_version','.subsections_via_symbols','.loh')):continue
        if s.startswith('.section'):
            if '__text,' in s:line='.text'
            elif '__cstring,' in s or '__literal16,' in s:line='.section .rodata,"a"'
            else:raise ValueError('unexpected section '+s)
        line=line.split(';')[0];lines.append(re.sub(r'([A-Za-z_.$][\w.$]*)@PAGEOFF',r':lo12:\1',line).replace('@PAGE',''))
    # Original second instruction is PC-relative: rebuild it, never copy it.
    lines+=['.text','.p2align 2','.global _dvm_original_dispatch','_dvm_original_dispatch:',
            'bti c','adrp x3, _dvm_dispatch_table_page','b _dvm_dispatch_continue']
    (a.out/'shim-elf.s').write_text('\n'.join(lines)+'\n')
    (a.out/'layout.ld').write_text(f'SECTIONS {{ . = {CAVE:#x}; .text : {{ *(.text) }} .rodata : {{ *(.rodata) }} /DISCARD/ : {{ *(.comment) *(.note*) }} }}\n'+''.join(f'{k} = {v:#x};\n' for k,v in LINKS.items()))
    subprocess.run(['xcrun','clang','-target','aarch64-none-elf','-march=armv8.3-a','-c',str(a.out/'shim-elf.s'),'-o',str(a.out/'shim.o')],check=True)
    subprocess.run(['ld.lld','-T',str(a.out/'layout.ld'),'-e','_dvm_surface_pin',str(a.out/'shim.o'),'-o',str(a.out/'shim.elf')],check=True)
    subprocess.run(['/opt/homebrew/opt/llvm/bin/llvm-objcopy','-O','binary',str(a.out/'shim.elf'),str(a.out/'shim.bin')],check=True)
    symbols=subprocess.check_output(['/opt/homebrew/opt/llvm/bin/llvm-nm','--defined-only','--numeric-sort',str(a.out/'shim.elf')],text=True)
    (a.out/'symbols.txt').write_text(symbols);table={p[2]:int(p[0],16) for l in symbols.splitlines() if len(p:=l.split())==3}
    payload=(a.out/'shim.bin').read_bytes()
    if len(payload)>END-CAVE:raise ValueError('payload capacity')
    patched=bytearray(b);patches=[]
    def put(va,value):
        off=va-BASE;patches.append(dict(address=hex(va),before=patched[off:off+len(value)].hex(),after=value.hex()));patched[off:off+len(value)]=value
    put(ENTRY,struct.pack('<I',0xd503245f)+branch(ENTRY+4,table['_dvm_surface_pin']))
    so,va,old=preceding;size=CAVE+len(payload)-va
    put(BASE+so+32,struct.pack('<Q',size));put(BASE+so+48,struct.pack('<Q',size))
    patched[CAVE-BASE:CAVE-BASE+len(payload)]=payload
    (a.out/'bootkc').write_bytes(patched)
    report=dict(scope='temporary caller-memory pin/complete probe; no host page export or GPU import',source_sha256=SHA,
        output_sha256=hashlib.sha256(patched).hexdigest(),payload_address=hex(CAVE),payload_bytes=len(payload),
        payload_sha256=hashlib.sha256(payload).hexdigest(),patches=patches,guards={hex(k):v for k,v in GUARDS.items()},
        abi_checks=CHECKS,links={k:hex(v) for k,v in LINKS.items()},compile_command=cmd,sptm_modified=False,txm_modified=False)
    (a.out/'ledger.json').write_text(json.dumps(report,indent=2)+'\n');print(a.out)
if __name__=='__main__':main()
