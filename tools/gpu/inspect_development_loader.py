#!/usr/bin/env python3
"""Bounded, read-only exact-guest development-loader contract evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bootkc',type=Path,required=True)
    p.add_argument('--txm',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    images={
        'kernel':(a.bootkc,0xfffffff007004000,
            '41abd56f8295dbc83d9131857357909c035ca0a33b244f5614f16e03185947c9',
            [(0xfffffff0091b009c,70),(0xfffffff0091acb18,6),
             (0xfffffff0091ac43c,65),(0xfffffff00b304534,55),
             (0xfffffff00afc937c,130),(0xfffffff00b045b40,58),
             (0xfffffff00b0429b8,7)]),
        'txm':(a.txm,0xfffffff017004000,
            'b8617cfca055a03711247ad9652f3cfdb026889dcca2eb1436496df4cb61a398',
            [(0xfffffff01703b1e8,11),(0xfffffff01703b33c,5),
             (0xfffffff017033590,54),(0xfffffff017033a64,74)])}
    report={'scope':'static exact-guest evidence, not a runtime branch trace','images':{}}
    for name,(path,base,digest,ranges) in images.items():
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=digest:raise ValueError(name+' identity')
        report['images'][name]={'path':str(path.resolve()),'sha256':digest}
        if name=='txm':
            offset=0xfffffff01703b684-base+4*(41-1)
            target=0xfffffff01703b208+struct.unpack_from('<i',data,offset)[0]
            if target!=0xfffffff01703b33c:raise ValueError('TXM selector 41 dispatch')
            report['selector_41']={'table_entry_file_offset':hex(offset),'target':hex(target),
                'call_site':'0xfffffff01703b348','handler':'0xfffffff017033590',
                'developer_mode_gate':'0xfffffff0170335b0',
                'get_task_allow_gate':'0xfffffff017033618'}
    a.out.mkdir(exist_ok=False)
    for name,(path,base,digest,ranges) in images.items():
        command='e scr.color=0; '+''.join(f's {va:#x}; pd {count}; ' for va,count in ranges)+'q'
        args=['r2','-q','-n','-a','arm','-b','64','-m',hex(base),'-c',command,str(path)]
        result=subprocess.run(args,capture_output=True,check=True,timeout=30)
        (a.out/(name+'.disass')).write_bytes(result.stdout)
        report['images'][name]['command']=args
        report['images'][name]['disassembly_sha256']=hashlib.sha256(result.stdout).hexdigest()
    (a.out/'evidence.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
