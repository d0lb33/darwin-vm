#!/usr/bin/env python3
"""Extract byte-identical MTLB slices/AIR for inspection; never rewrite target metadata.
MTLB layout reference: https://github.com/YuAo/MetalLibraryArchive
"""
import argparse, hashlib, json, struct
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('library',type=Path); p.add_argument('out',type=Path); a=p.parse_args()
a.out.mkdir(parents=True,exist_ok=False); raw=a.library.read_bytes(); slices=[]
if raw[:4] == bytes.fromhex('cafebabe'):
 n=struct.unpack_from('>I',raw,4)[0]; assert n<100
 for i in range(n):
  cpu,sub,off,size,align=struct.unpack_from('>5I',raw,8+i*20)
  assert off+size<=len(raw); slices.append((off,raw[off:off+size],cpu,sub))
else: slices=[(0,raw,None,None)]
records=[]
for i,(base,d,cpu,sub) in enumerate(slices):
 r=dict(index=i,offset=base,size=len(d),cpu=cpu,subtype=sub,sha256=hashlib.sha256(d).hexdigest(),magic=d[:4].hex());records.append(r)
 (a.out/f'slice{i}.metallib').write_bytes(d)
 if d[:4]!=b'MTLB': continue
 assert struct.unpack_from('<Q',d,16)[0] == len(d)
 r['target_header']=d[4:16].hex(); foff,fsz,pub,psz,priv,vsz,bc,bsz=struct.unpack_from('<8Q',d,24)
 assert bc+bsz <= len(d)
 n=struct.unpack_from('<I',d,foff)[0]; cursor=foff+4; funcs=[]
 for j in range(n):
  length=struct.unpack_from('<I',d,cursor)[0]; end=cursor+length; q=cursor+4; tags={}
  assert end <= len(d)
  while q < end:
   tag=d[q:q+4]; q+=4
   if tag==b'ENDT': break
   size=struct.unpack_from('<H',d,q)[0];q+=2;tags[tag]=d[q:q+size];q+=size
  name=tags[b'NAME'].rstrip(b'\0').decode(); assert '/' not in name and '\\' not in name
  off=struct.unpack('<3Q',tags[b'OFFT'])[2];size=struct.unpack('<Q',tags[b'MDSZ'])[0]
  blob=d[bc+off:bc+off+size]; assert len(blob)==size and hashlib.sha256(blob).digest()==tags[b'HASH']
  (a.out/f'{name}.air').write_bytes(blob)
  funcs.append(dict(name=name,type=tags[b'TYPE'][0],offset=base+bc+off,size=size,sha256=hashlib.sha256(blob).hexdigest(),version=list(struct.unpack('<4H',tags[b'VERS']))))
  cursor=end
 r['functions']=funcs
(a.out/'index.json').write_text(json.dumps(dict(source=str(a.library),sha256=hashlib.sha256(raw).hexdigest(),slices=records),indent=2)+'\n')
print(json.dumps([{k:v for k,v in r.items() if k!='functions'} | {'functions':len(r.get('functions',[]))} for r in records],indent=2))
