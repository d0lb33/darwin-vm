#!/usr/bin/env python3
"""Opt-in exact-24A5430a development-loader experiments; no SPTM/TXM edits."""
import argparse,hashlib,json,re,shutil,struct,subprocess
from pathlib import Path
from build_boot_transport import branch
BASE=0xfffffff007004000
SHA='41abd56f8295dbc83d9131857357909c035ca0a33b244f5614f16e03185947c9'
CAVE=0xfffffff00aa5c180
END=0xfffffff00aa60000
GUARDS={
 0xfffffff00b1f1a10:'5f2403d59b660514fc6f01a9fa6702a9',
 0xfffffff0091b009c:'7f2303d5ffc300d1f44f01a9fd7b02a9',
 0xfffffff00b045b40:'7f2303d5ff4302d1f44f07a9fd7b08a9',
 0xfffffff00afc937c:'7f2303d5f657bda9f44f01a9fd7b02a9',
 0xfffffff00b1720e8:'7f2303d5fd7bbfa9fd030091acf8e797',
 0xfffffff0091ac43c:'7f2303d5f657bda9f44f01a9fd7b02a9',
 0xfffffff00b005b2c:'7f2303d5fd7bbfa9fd030091010c40f9',
 0xfffffff00b005a14:'7f2303d5fd7bbfa9fd03009161aaed97',
 0xfffffff0091c88d8:'7176ffd031e21291300240f9110a1fd7',
 0xfffffff00b34b480:'7f2303d5f85fbca9f65701a9f44f02a9',
 0xfffffff0091acb18:'3000009010720291510f86d23002c1da10cd04f9',
 0xfffffff00afc93dc:'74a21d916822513948020836',
 0xfffffff00b045b6c:'2805805229008052e81300b9e91f00b9',
 0xfffffff00b0429b8:'5f2403d5086efed0084945f9480000b4',
}
LINKS={'_dvm_current_proc':0xfffffff00b1720e8,'_dvm_entitled':0xfffffff0091ac43c,
 '_dvm_cs_allow_invalid':0xfffffff00afc937c,'_dvm_csflags':0xfffffff00b005b2c,
 '_dvm_pid':0xfffffff00b005a14,'_dvm_printf':0xfffffff0091c88d8,
 '_dvm_transport_uc':0xfffffff00b34b480,'_dvm_developer_mode':0xfffffff00b0429b8}

def commands(b,h):
    o=h+32
    for _ in range(struct.unpack_from('<I',b,h+16)[0]):
        c,n=struct.unpack_from('<II',b,o)
        if n<8 or o+n>len(b):raise ValueError('Mach-O command extent')
        yield c,o;o+=n

def build(a):
    links=dict(LINKS)
    guards=dict(GUARDS)
    hooks=[('policy',0xfffffff0091b009c),('monitor',0xfffffff00b045b40)]
    if a.file_policy:
        guards.update({0xfffffff0091ae5c8:'57010037f65340f9f35b00a9e023fff0',
                       0xfffffff00b31e2dc:'7f2303d5ff0302d1fc6f02a9fa6703a9',
                       0xfffffff0091c8ec8:'7176ffd031c21e91300240f9110a1fd7'})
        links['_dvm_blob_flags']=0xfffffff0091c8ec8
        links['_dvm_ct_accept']=0xfffffff0091ae5f0
        links['_dvm_ct_reject']=0xfffffff0091ae5cc
        hooks.append(('mmap',0xfffffff00b31e2dc))
    if a.file_policy>=3:
        guards.update({0xfffffff0091ad774:'7f2303d5fc6fbaa9fa6701a9f85f02a9',
                       0xfffffff0091aee08:'e06f40f9800000b4e4160094f60300aa',
                       0xfffffff0091ae768:'480340b9e9ff9752e97fbe720801090a'})
        hooks.append(('signature',0xfffffff0091ad774))
        links['_dvm_ct_complete']=0xfffffff0091aee08
    if a.file_policy>=4:
        guards.update({0xfffffff00afc9af4:'5f2403d5610000b4084c40f928000039',
                       0xfffffff00b044f3c:'7f2303d5ff8302d1f65707a9f44f08a9',
                       0xfffffff00b04501c:'7f2303d5ff8302d1f65707a9f44f08a9',
                       0xfffffff00b044f6c:'a90380524a008052e91300b9ea1f00b9',
                       0xfffffff00b04504c:'c9038052e91300b929008052e95f0039'})
        links.update(_dvm_blob_hash=0xfffffff00afc9af4,
                     _dvm_authorize_hash=0xfffffff00b044f3c,_dvm_match_hash=0xfffffff00b04501c)
    if a.file_policy>=5:
        guards[0xfffffff0091ac980]='0000a252b97100940000a452b7710094'
    b=a.bootkc.read_bytes()
    if hashlib.sha256(b).hexdigest()!=SHA:raise ValueError('requires pinned managed-export BootKC')
    for va,value in guards.items():
        if b[va-BASE:va-BASE+len(bytes.fromhex(value))]!=bytes.fromhex(value):raise ValueError(f'ABI guard {va:x}')
    if any(b[CAVE-BASE:END-BASE]):raise ValueError('RX padding is not empty')
    # Prove this padding belongs to the outer executable mapping and overlaps
    # no inner fileset segment. Extend only its immediate preceding RX segment.
    outer=False;preceding=None;entry_name=None
    for c,o in commands(b,0):
        if c==0x19:
            va,vm,fo,fs,mp,ip=struct.unpack_from('<QQQQii',b,o+24)
            if ip&4 and va<=CAVE and END<=va+fs and fo==va-BASE:outer=True
        if c!=0x80000035:continue
        h,no=struct.unpack_from('<QI',b,o+16);name=b[o+no:b.index(0,o+no)].decode()
        for sc,so in commands(b,h):
            if sc!=0x19:continue
            va,vm,fo,fs,mp,ip=struct.unpack_from('<QQQQii',b,so+24)
            if vm and va<END and CAVE<va+vm:raise ValueError('padding overlaps fileset '+name)
            if ip&4 and va+fs==0xfffffff00aa5c16c:
                if vm!=fs or fo!=va-BASE:raise ValueError('preceding segment mapping')
                preceding=(so,va,fs);entry_name=name
    if not outer or preceding is None:raise ValueError('RX padding ownership')
    a.out.mkdir(exist_ok=False);src=Path(__file__).with_name('development_loader_shim.cpp')
    shutil.copyfile(src,a.out/src.name);shutil.copyfile(__file__,a.out/Path(__file__).name)
    sdk=subprocess.check_output(['xcrun','--show-sdk-path'],text=True).strip()
    cmd=['xcrun','clang++','-target','arm64e-apple-ios27.0','-isysroot',sdk,'-I',sdk+'/System/Library/Frameworks/Kernel.framework/Headers',
         '-DKERNEL',f'-DDVM_LOADER_FIX={a.file_policy}','-mkernel','-fno-exceptions','-fno-rtti','-fno-stack-protector','-fno-builtin','-std=c++17','-Os','-S',str(src),'-o',str(a.out/'shim.s')]
    subprocess.run(cmd,check=True);asm=(a.out/'shim.s').read_text()
    if '.ptrauth_kernel_abi_version 0' not in asm:raise ValueError('kernel ABI')
    lines=[]
    for line in asm.splitlines():
        s=line.strip()
        if s.startswith(('.build_version','.ptrauth_kernel_abi_version','.subsections_via_symbols','.loh')):continue
        if s.startswith('.section'):
            if '__text,' in s:line='.text'
            elif '__cstring,' in s:line='.section .rodata,"a"'
            else:raise ValueError('unexpected section '+s)
        line=line.split(';')[0];line=re.sub(r'([A-Za-z_.$][\w.$]*)@PAGEOFF',r':lo12:\1',line).replace('@PAGE','');lines.append(line)
    for name,address in hooks:
        words=struct.unpack_from('<II',b,address-BASE)
        lines+=['.text','.p2align 2',f'.global _dvm_original_{name}',f'_dvm_original_{name}:']+[f'.inst {w:#x}' for w in words]+[f'b _dvm_{name}_continue']
        links[f'_dvm_{name}_continue']=address+8
    if a.file_policy:
        # Mid-function branch: preserve every caller-clobbered integer/SIMD
        # register and NZCV, unlike an ordinary ABI function entry wrapper.
        lines+=['.text','.p2align 2','.global _dvm_ct_branch','_dvm_ct_branch:',
                'tbz w23, #0, .Lct_scope','b _dvm_ct_accept','.Lct_scope:','sub sp, sp, #688']
        lines += [f'stp x{i}, x{i+1}, [sp, #{i*8}]' for i in range(0,18,2)]
        lines += ['stp x18, x30, [sp, #144]','mrs x16, nzcv','str x16, [sp, #160]']
        lines += [f'stp q{i}, q{i+1}, [sp, #{176+i*16}]' for i in range(0,32,2)]
        lines += ['mov x0, x19','mov x1, x21','ldr x2, [sp, #848]','mov x3, x26',
                  'bl _dvm_development_ct','cbz w0, .Lct_deny']
        for label,target in (('.Lct_allow','_dvm_ct_complete' if a.file_policy>=3 else '_dvm_ct_accept'),('.Lct_deny','_dvm_ct_reject')):
            lines += [label+':']
            lines += [f'ldp q{i}, q{i+1}, [sp, #{176+i*16}]' for i in range(0,32,2)]
            lines += ['ldr x16, [sp, #160]','msr nzcv, x16']
            lines += [f'ldp x{i}, x{i+1}, [sp, #{i*8}]' for i in range(0,18,2)]
            lines += ['ldp x18, x30, [sp, #144]','add sp, sp, #688',f'b {target}']
    (a.out/'shim-elf.s').write_text('\n'.join(lines)+'\n')
    ld=f'SECTIONS {{ . = {CAVE:#x}; .text : {{ *(.text) }} .rodata : {{ *(.rodata) }} /DISCARD/ : {{ *(.comment) *(.note*) }} }}\n'
    (a.out/'layout.ld').write_text(ld+''.join(f'{name} = {va:#x};\n' for name,va in links.items()))
    subprocess.run(['xcrun','clang','-target','aarch64-none-elf','-march=armv8.3-a','-c',str(a.out/'shim-elf.s'),'-o',str(a.out/'shim.o')],check=True)
    subprocess.run(['ld.lld','-T',str(a.out/'layout.ld'),'-e','_dvm_development_uc',str(a.out/'shim.o'),'-o',str(a.out/'shim.elf')],check=True)
    llvm='/opt/homebrew/opt/llvm/bin/'
    subprocess.run([llvm+'llvm-objcopy','-O','binary',str(a.out/'shim.elf'),str(a.out/'shim.bin')],check=True)
    syms=subprocess.check_output([llvm+'llvm-nm','--defined-only','--numeric-sort',str(a.out/'shim.elf')],text=True)
    (a.out/'symbols.txt').write_text(syms)
    table={p[2]:int(p[0],16) for l in syms.splitlines() if len(p:=l.split())==3}
    payload=(a.out/'shim.bin').read_bytes()
    if len(payload)>END-CAVE:raise ValueError('RX padding capacity')
    patched=bytearray(b);patches=[]
    def put(address,value,purpose):
        off=address-BASE;old=patched[off:off+len(value)];patched[off:off+len(value)]=value
        patches.append(dict(address=hex(address),before=old.hex(),after=value.hex(),purpose=purpose))
    for name,address in [('uc',0xfffffff00b1f1a10)]+hooks:
        put(address,struct.pack('<I',0xd503245f)+branch(address+4,table['_dvm_development_'+name]),name)
    if a.file_policy:put(0xfffffff0091ae5c8,branch(0xfffffff0091ae5c8,table['_dvm_ct_branch']),'scoped ad-hoc CT decision')
    if a.file_policy>=5:
        # Keep only the compilation-service capability in this isolated test
        # image. The adjacent local-signing disable and TXM remain unchanged.
        put(0xfffffff0091ac984,struct.pack('<I',0xd503201f),'retain kernel compilation-service capability on iPhone')
    so,va,old=preceding;size=CAVE+len(payload)-va
    put(BASE+so+32,struct.pack('<Q',size),'preceding RX VM extent')
    put(BASE+so+48,struct.pack('<Q',size),'preceding RX file extent')
    patched[CAVE-BASE:CAVE-BASE+len(payload)]=payload
    (a.out/'bootkc').write_bytes(patched)
    shutil.copyfile(a.dtree,a.out/'system.dtree')
    report=dict(scope='opt-in development worker only; monitor status and actual revised driver execution required',
        source_bootkc=str(a.bootkc.resolve()),source_sha256=SHA,output_sha256=hashlib.sha256(patched).hexdigest(),
        payload_address=hex(CAVE),payload_bytes=len(payload),payload_sha256=hashlib.sha256(payload).hexdigest(),
        file_policy_fix=a.file_policy,preceding_fileset=entry_name,patches=patches,guards={hex(k):v for k,v in guards.items()},links={k:hex(v) for k,v in links.items()},
        output_bootkc_sha256=hashlib.sha256(patched).hexdigest(),output_dtree_sha256=hashlib.sha256(a.dtree.read_bytes()).hexdigest(),
        source_dtree=str(a.dtree.resolve()),compile_command=cmd,sptm_modified=False,txm_modified=False,normal_transport_preserved=True)
    (a.out/'ledger.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bootkc',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--dtree',type=Path,required=True)
    p.add_argument('--file-policy',type=int,choices=(0,1,2,3,4,5),default=0,help='0: prior probe; 1: CT gate; 2: also RX mmap; 3: AMFI completion; 4: stock TXM compilation-hash authorization; 5: retain kernel compilation capability')
    build(p.parse_args())
