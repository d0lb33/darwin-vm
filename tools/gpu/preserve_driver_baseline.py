#!/usr/bin/env python3
"""Preserve a passed GPU baseline, with no /tmp boot dependencies.

Read-only source; flatten/compare into a new directory. Preserve exact inputs,
including the transport DT, rather than regenerating a different device tree.
The output is an input to run_guest_load.py, not an ordinary VM launcher.
"""
import argparse, json, shutil, subprocess, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from checkpoint_common import sha256, verify_backing_chain, qcow2_backing_chain
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--manifest',type=Path,required=True)
p.add_argument('--trial',type=Path,required=True)
p.add_argument('--build',type=Path,required=True)
p.add_argument('--cache',type=Path,required=True)
p.add_argument('--air',type=Path,required=True)
p.add_argument('--out',type=Path,required=True)
p.add_argument('--consumer',action='store_true',help='preserve verified offscreen CARenderer scope, not displayed-blur scope')
a=p.parse_args();m=json.loads(a.manifest.read_text())
r=json.loads((a.trial/'result.json').read_text())
if not r.get('passed') or not r.get('driver_present') or r.get('source_manifest_sha256')!=sha256(a.manifest):
    p.error('requires passed GPU trial of this exact manifest')
consumer=r.get('driver',{}).get('scope')=='exact-guest-CARenderer-64x64-red-CALayer'
if consumer!=a.consumer:p.error('consumer and displayed-blur preservation scopes must match')
if consumer and not json.loads((a.trial/'consumer-verification.json').read_text()).get('verified'):
    p.error('missing verified consumer evidence')
installation=m.get('guest_installation',{})
if Path(installation.get('build','')).resolve()!=a.build.resolve():p.error('build does not match installed helper provenance')
if installation.get('cache_sha256')!=sha256(a.cache):p.error('cache does not match installer preimage')
for path,digest in installation.get('inputs',{}).items():
    if sha256(Path(path))!=digest:p.error('changed installed build input: '+path)
verify_backing_chain(m['disk']['backing_chain'])
if Path(m['disk']['path']).stat().st_mode & 0o222:p.error('source must be sealed read-only')
for path,entry in m['qemu_inputs'].items():
    if sha256(Path(path))!=entry['sha256']:p.error('changed pinned input: '+path)
if sha256(a.air)!='8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364':
    p.error('unexpected exact-guest AIR')
a.out=a.out.resolve();a.out.mkdir(parents=True,exist_ok=False)
qimg=Path(__file__).resolve().parents[2]/'qemu-sptm/build/qemu-img'
disk=a.out/'system.qcow2'
subprocess.run([str(qimg),'convert','-f','qcow2','-O','qcow2',m['disk']['path'],str(disk)],check=True)
subprocess.run([str(qimg),'compare','-f','qcow2','-F','qcow2',m['disk']['path'],str(disk)],check=True)
disk.chmod(0o444)
argv=m['qemu_argv'];inputs={}
for index,name in [(0,'qemu-system-aarch64')]+[(argv.index(flag)+1,flag[1:]) for flag in ('-bootkc','-dtree','-tc','-ramdisk','-sptm','-txm')]:
    dest=a.out/name;shutil.copy2(argv[index],dest);dest.chmod(0o555 if index==0 else 0o444)
    argv[index]=str(dest);inputs[str(dest)]={'bytes':dest.stat().st_size,'sha256':sha256(dest)}
argv[argv.index('-drive')+1]=f'if=none,id=ans,file={disk},format=qcow2'
shutil.copytree(a.build,a.out/'driver-build')
for f in (a.out/'driver-build').rglob('*'):
    if f.is_file():f.chmod(0o555 if f.stat().st_mode & 0o111 else 0o444)
shutil.copyfile(a.cache,a.out/'launchd.plist')
shutil.copyfile(a.air,a.out/'QuartzCore.metallib')
shutil.copyfile(qimg,a.out/'qemu-img')
# Runtime output paths are recreated by the owned runner.
env={k:v for k,v in m['qemu_env'].items() if k not in ('DARWIN_TOUCH_EVENTS','DARWIN_INPUT_STATUS','DARWIN_DCP_GPU_PRESENT_DIR')}
out=dict(format=m['format'],battery_source=m.get('battery_source'),qemu_argv=argv,qemu_env=env,qemu_inputs=inputs,
    disk=dict(path=str(disk),backing_chain=qcow2_backing_chain(qimg,disk)))
(a.out/'control.json').write_text(json.dumps(out,indent=2)+'\n')
(a.out/'source-manifest.json').write_text(a.manifest.read_text())
(a.out/'provenance.json').write_text(json.dumps(dict(source_manifest=str(a.manifest.resolve()),source_manifest_sha256=sha256(a.manifest),
    passed_trial=str(a.trial.resolve()),trial_result_sha256=sha256(a.trial/'result.json'),disk_compared_equal=True,
    air_sha256=sha256(a.air),launchd_cache_sha256=sha256(a.cache),build=str(a.build.resolve()),
    workload_scope=r.get('driver',{}).get('scope'),
    scope='flattened immutable disk and exact boot inputs; use disposable children; no checkpoint or new runtime validation implied'),indent=2)+'\n')
verify_backing_chain(m['disk']['backing_chain'])
print(a.out)
