#!/usr/bin/env python3
"""Read the known DataMigrator dependency-wait frame in a paused 24A5430a VM."""
import argparse
import json
from pathlib import Path
from inspect_migration_processes import DemandMemory
from warm_boot_postmortem import inspect, u64

def string(m, root, ptr):
    if not ptr:return None
    b=m.user(root,ptr,32)
    flags=int.from_bytes(b[8:10],'little')
    if flags == 0x7c8:
        address,length=u64(b,16),u64(b,24)
    elif flags == 0x78c:
        address,length=ptr+17,b[16]
    else:return {'address':hex(ptr),'unsupported_flags':hex(flags)}
    if length > 4096:raise ValueError('string exceeds bound')
    return m.user(root,address,length).decode('utf-8','replace')

def set_members(m,root,ptr,slide):
    # __NSSetM native enumerator 0x1806206cc..0x180620714, slide 0x16294000.
    if u64(m.user(root,ptr,8),0) & 0x7fffffff8 != 0x1e6f3c990+slide:
        raise ValueError('expected the verified __NSSetM class')
    storage=int.from_bytes(m.user(root,0x1e3f57308+slide,4),'little')
    b=m.user(root,ptr+storage,16)
    index=(int.from_bytes(b[12:16],'little') >> 23) & 0x1f8
    capacity=int.from_bytes(m.user(root,0x18097aa58+slide+index,4),'little')
    if not 0 <= capacity <= 4096:raise ValueError('set capacity exceeds bound')
    table=m.user(root,u64(b,0),capacity*8)
    return [u64(table,i) for i in range(0,len(table),8) if u64(table,i) not in (0,0x1e8d94790+slide)]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--monitor', required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--proc', type=lambda x:int(x,0), required=True)
    p.add_argument("--executable-base", type=lambda x:int(x,0), default=0x104170000)
    p.add_argument("--cache-slide", type=lambda x:int(x,0), default=0x16294000)
    a = p.parse_args()
    m = DemandMemory(Path(a.monitor), a.out/'pages')
    raw = m.kernel(a.proc,0x800)
    if raw[0x55c:0x56d].split(b'\0')[0] != b'com.apple.datami':
        raise RuntimeError('wrong process')
    d = inspect(m,'com.apple.datami',0,raw)
    root=int(d['root'],16)
    if m.user(root,a.executable_base,4) != bytes.fromhex('cffaedfe'):
        raise RuntimeError('executable base is not a Mach-O header')
    for t in d['threads']:
        if hex(a.executable_base+0xf170) not in t.get('frames',[]):continue
        saved=u64(m.kernel(int(t['thread'],16)+0x110,8),0)
        fp=u64(m.kernel(saved+0xf0,8),0)
        t['frame_records']=[]
        for _ in range(12):
            if not fp:break
            b=m.user(root,fp-0x60,0x80)
            t['frame_records'].append({'fp':hex(fp),'words':[hex(u64(b,i)) for i in range(0,0x80,8)]})
            parent=u64(b,0x60)
            if parent<=fp:break
            fp=parent
        t['heap_candidates']={}
        for f in t['frame_records']:
            for value in f['words']:
                ptr=int(value,16)
                if 0x7000000000 <= ptr < 0x8000000000 and value not in t['heap_candidates']:
                    try:t['heap_candidates'][value]=m.user(root,ptr,0x80).hex()
                    except ValueError:pass
        t['dependency_blocks']=[]
        for address, h in t['heap_candidates'].items():
            b=bytes.fromhex(h)
            if u64(b,16) != a.executable_base+0xef18:continue
            record={'block':address,'pending':[],'completed':[]}
            for ptr in set_members(m,root,u64(b,40),a.cache_slide):
                obj=m.user(root,ptr,0x68)
                if u64(obj,0) & 0x7fffffff8 != a.executable_base+0x30b20:
                    raise ValueError(f'pending member {ptr:#x} isa={u64(obj,0):#x} is not DataMigrationPlugin')
                record['pending'].append({'address':hex(ptr),'identifier':string(m,root,u64(obj,8)),
                    'name':string(m,root,u64(obj,24)), 'dependency':string(m,root,u64(obj,56)),
                    'flags':list(obj[32:36])})
            record['completed']=[string(m,root,ptr) for ptr in set_members(m,root,u64(b,48),a.cache_slide)]
            t['dependency_blocks'].append(record)
        t['pointed_registers']={}
        for reg in ['x19','x20','x27','x28']:
            ptr=int(t['registers'][reg],16)
            try:t['pointed_registers'][reg]=m.user(root,ptr,0x80).hex()
            except ValueError as e:t['pointed_registers'][reg]=str(e)
    (a.out/'wait.json').write_text(json.dumps(d,indent=2)+'\n')

if __name__=='__main__':main()
