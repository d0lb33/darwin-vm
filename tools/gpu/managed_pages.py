"""Read only the kernel-registered resource from this trial's managed DRAM."""
import os,struct
from pathlib import Path
PAGE=16384
COUNT=759
LENGTH=PAGE*COUNT

def read_resource(out):
    out=Path(out);record=(out/'managed-pages.bin').read_bytes()
    if len(record)!=32+COUNT*8:raise ValueError('managed registration extent')
    with (out/'shared-ram.bin').open('rb') as f:f.seek(16);session=f.read(16)
    length,count=struct.unpack_from('<QQ',record,16)
    offsets=struct.unpack_from('<759Q',record,32)
    if record[:16]!=session or (length,count)!=(LENGTH,COUNT):raise ValueError('managed identity/geometry')
    if len(set(offsets))!=COUNT or any(x%PAGE or x>0x300000000-PAGE for x in offsets):raise ValueError('managed page ranges')
    with (out/'managed-ram.bin').open('rb') as f:
        if os.fstat(f.fileno()).st_size!=0x300000000:raise ValueError('managed DRAM extent')
        data=b''.join(os.pread(f.fileno(),PAGE,x) for x in offsets)
    if len(data)!=LENGTH:raise ValueError('managed short read')
    return data
