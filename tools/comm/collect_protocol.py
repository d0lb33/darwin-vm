#!/usr/bin/env python3
"""Read extended block signatures from an immutable exact-guest RAM snapshot.

The selector order comes from ipsw's dump of the same protocol's method list.
The arm64e optimized method representation omits block argument information;
protocol_t.extendedMethodTypes (+0x48) supplies that information to NSXPC.
"""
import argparse,json,mmap,re,struct,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'re'))
from warm_boot_postmortem import Memory
def main():
    p=argparse.ArgumentParser();p.add_argument('snapshot',type=Path);p.add_argument('objc_dump',type=Path);p.add_argument('out',type=Path);p.add_argument('--slide',type=lambda v:int(v,0),required=True);a=p.parse_args()
    processes=json.loads((a.snapshot/'process-stacks.json').read_text())
    root=int(next(v for v in processes if v['name']=='Preferences' and v.get('threads'))['root'],16)
    memory=Memory.__new__(Memory);memory.maps=[]
    for file in (a.snapshot/'ram').glob('*.bin'):
        with file.open('rb') as f:memory.maps.append((int(file.stem,16),mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ)))
    def read(v,n):return memory.user(root,v&0xffffffffffff,n)
    def ptr(v):return int.from_bytes(read(v,8),'little')&0xffffffffffff
    def string(v):return read(v,512).split(b'\0')[0].decode()
    proto=ptr(0x269533a58+a.slide) # exact CTCellularPlanClient protocol reference
    if string(ptr(proto+8))!='CTCellularPlanClient':raise ValueError('wrong protocol')
    count=struct.unpack('<II',read(ptr(proto+24),8))[1]
    selectors=re.findall(r'-\[CTCellularPlanClient ([^]]+)\];',a.objc_dump.read_text().split('@end')[0])
    if len(selectors)!=count or count!=78:raise ValueError('protocol inventory mismatch')
    ext=ptr(proto+72)
    rows=[dict(name=name,encoding=string(ptr(ext+index*8))) for index,name in enumerate(selectors)]
    with a.out.open('x') as f:json.dump(rows,f,indent=2)
if __name__=='__main__':main()
