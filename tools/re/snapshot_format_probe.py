#!/usr/bin/env python3
"""Disposable 24A5430a checkpoint experiment: change one SplashBoard cache byte.

Requires paused QEMU, known pid-700 migrator in APP_SPLASH_ASTC1 lineage,
verified native format-selector code, and an already listening GDB endpoint.
Uses raw physical-memory packets only; no breakpoint, guest call or execution.
This is diagnostic state, not a bootstrap fix. Close connection before timing.
"""
import argparse,json,socket
from pathlib import Path
from inspect_migration_processes import DemandMemory,identity
from warm_boot_postmortem import inspect

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--monitor',required=True)
p.add_argument('--port',required=True,type=int)
p.add_argument('--out',required=True,type=Path)
p.add_argument('--value',type=int,choices=[0,1],required=True)
a=p.parse_args()
m=DemandMemory(Path(a.monitor),a.out/'pages')
if 'paused' not in m.monitor.command('info status'):raise RuntimeError('VM must be paused')
raw=m.kernel(0xffffffeb4a9478e8,0x800)
if identity(raw)!=('com.apple.migrat',700):raise RuntimeError('different migrator')
d=inspect(m,'com.apple.migrat',0,raw);root=int(d['root'],16)
slide=0x16294000
# Native reads the two capability bytes then ANDs them; don't mutate another build.
va=0x2ce2f4380+slide
if m.user(root,0x2ac02de6c+slide,8).hex()!='08014e393401080a':raise RuntimeError('different format selector')
if m.user(root,0x2ce2f4388+slide,8)!=b'\xff'*8:raise RuntimeError('capabilities not initialized')
before=m.user(root,va,1)
if before!=b'\x01':raise RuntimeError('expected ASTC capability true')
table=root&0x0000fffffffffc00
for level,shift in ((1,36),(2,25),(3,14)):
 idx=(va>>shift)&(0x7f if level==1 else 0x7ff)
 entry=int.from_bytes(m.physical(table+idx*8,8),'little')
 if entry&3!=3:raise RuntimeError('requires mapped 16K leaf tables')
 if level==3:pa=(entry&0x0000ffffffffc000)|(va&0x3fff)
 else:table=entry&0x0000ffffffffc000

with socket.create_connection(('127.0.0.1',a.port),timeout=5) as sock:
 def packet(s):
  data=s.encode();sock.sendall(b'$'+data+b'#'+f'{sum(data)%256:02x}'.encode())
  while True:
   marker=sock.recv(1)
   if not marker:raise RuntimeError('closed GDB connection')
   if marker==b'$':break
  out=b''
  while True:
   ch=sock.recv(1)
   if not ch:raise RuntimeError('closed GDB connection')
   if ch==b'#':break
   out+=ch
  checksum=sock.recv(2)
  if int(checksum,16)!=sum(out)%256:raise RuntimeError('GDB checksum mismatch')
  sock.sendall(b'+');return out.decode()
 if packet('Qqemu.PhyMemMode:1')!='OK':raise RuntimeError('physical mode failed')
 try:
  if packet(f'm{pa:x},1')!='01':raise RuntimeError('live capability differs')
  if packet(f'M{pa:x},1:{a.value:02x}')!='OK':raise RuntimeError('write failed')
  if packet(f'm{pa:x},1')!=f'{a.value:02x}':raise RuntimeError('readback failed')
 finally:
  if packet('Qqemu.PhyMemMode:0')!='OK':raise RuntimeError('restore virtual mode failed')
# Closing a raw connection must not change execution state; enforce before timing.
m.monitor.command('stop')
status=m.monitor.command('info status')
if 'paused' not in status:raise RuntimeError('failed to leave guest paused')
result=dict(virtual=hex(va),physical=hex(pa),before=1,after=a.value,paused=True,
            debugger_disconnected=True,diagnostic_only=True)
(a.out/'mutation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
