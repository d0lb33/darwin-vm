#!/usr/bin/env python3
"""Combine reviewed HID disk lineage with already-proven native RTC inputs.

Pins an immutable copy of the newly built QEMU; no parent disk is edited.
Only QEMU, BootKC, DT and RTC selection differ from the source manifest.
"""
import argparse,copy,json,shutil,sys
from pathlib import Path
from aux_namespace_dt import extend
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from checkpoint_common import sha256,verify_backing_chain
p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('native_rtc',type=Path);p.add_argument('qemu',type=Path);p.add_argument('out',type=Path);a=p.parse_args()
m=json.loads(a.source.read_text());native=json.loads(a.native_rtc.read_text())
verify_backing_chain(m['disk']['backing_chain'])
for config in (m,native):
 for path,expected in config['qemu_inputs'].items():
  if sha256(Path(path))!=expected['sha256']:raise ValueError('changed pinned input '+path)
for key in ('-sptm','-txm'):
 lhs=Path(m['qemu_argv'][m['qemu_argv'].index(key)+1]);rhs=Path(native['qemu_argv'][native['qemu_argv'].index(key)+1])
 if sha256(lhs)!=sha256(rhs):raise ValueError('firmware mismatch '+key)
if native['qemu_env']['DARWIN_RTC_PV']!='0':raise ValueError('template is not native RTC')
a.out.mkdir(exist_ok=False);qemu=a.out/'qemu-system-aarch64';shutil.copy2(a.qemu,qemu);qemu.chmod(0o555)
dt=a.out/'aux.dtree';original=Path(native['qemu_argv'][native['qemu_argv'].index('-dtree')+1]);dt.write_bytes(extend(original.read_bytes()))
before=copy.deepcopy(m)
for index,path in ((0,qemu),(m['qemu_argv'].index('-bootkc')+1,Path(native['qemu_argv'][native['qemu_argv'].index('-bootkc')+1])),(m['qemu_argv'].index('-dtree')+1,dt)):
 old=Path(m['qemu_argv'][index]).resolve()
 for key in list(m['qemu_inputs']):
  if Path(key).resolve()==old:del m['qemu_inputs'][key]
 m['qemu_argv'][index]=str(path.resolve());m['qemu_inputs'][str(path.resolve())]=dict(bytes=path.stat().st_size,sha256=sha256(path))
m['qemu_env']['DARWIN_RTC_PV']='0'
m['driver_source_manifest']=str(a.source.resolve());m['driver_native_rtc_template']=str(a.native_rtc.resolve())
(a.out/'warm-manifest.json').write_text(json.dumps(m,indent=2)+'\n')
(a.out/'baseline.json').write_text(json.dumps(dict(source=str(a.source),source_sha256=sha256(a.source),native_template=str(a.native_rtc),native_template_sha256=sha256(a.native_rtc),qemu_sha256=sha256(qemu),disk_unchanged=m['disk']==before['disk'],dt_unrelated_properties_verified=True),indent=2)+'\n')
