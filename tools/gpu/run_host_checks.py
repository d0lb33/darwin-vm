#!/usr/bin/env python3
"""Repeat exact-guest UI shader loading/execution with bounded subprocess receipts."""
import argparse, hashlib, json, shutil, subprocess, sys, time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('libraries',type=Path);p.add_argument('out',type=Path);a=p.parse_args()
a.out=a.out.resolve();a.out.mkdir(parents=True,exist_ok=False);a.libraries=a.libraries.resolve()
root=Path(__file__).resolve().parent; receipts=[]
def run(label,cmd,deadline=60,allowed=(0,)):
    started=time.monotonic()
    r=subprocess.run([str(x) for x in cmd],capture_output=True,timeout=deadline)
    (a.out/(label+'.stdout')).write_bytes(r.stdout);(a.out/(label+'.stderr')).write_bytes(r.stderr)
    receipts.append(dict(label=label,command=[str(x) for x in cmd],returncode=r.returncode,seconds=time.monotonic()-started))
    (a.out/'commands.json').write_text(json.dumps(receipts,indent=2)+'\n')
    if r.returncode not in allowed: raise RuntimeError(f'{label} exit={r.returncode}; see stderr')
    return r
run('host',['sw_vers']);run('compiler',['xcrun','clang','--version'])
for stem,frameworks in [('metal_library_probe',['Foundation','Metal']),('metal_copy_probe',['Foundation','Metal','IOSurface'])]:
    cmd=['xcrun','clang','-fobjc-arc','-Wno-deprecated-declarations','-Wall','-Wextra','-Werror',root/(stem+'.m'),'-o',a.out/stem]
    for f in frameworks:cmd+=['-framework',f]
    run('build-'+stem,cmd)
qc=a.libraries/'QuartzCore.framework.default.metallib'
run('extract-air',[sys.executable,root/'extract_air.py',qc,a.out/'air'])
for label,lib in [('qc-fat',qc),('qc-air',a.out/'air/slice0.metallib'),('qc-archive',a.out/'air/slice1.metallib'),('rb-air',a.libraries/'RenderBox.framework.default.metallib'),('rb-archive',a.libraries/'RenderBox.framework.archive.metallib')]:
    run('inventory-'+label,[a.out/'metal_library_probe',lib],allowed=(0,1))
for label,lib in [('fat',qc),('air',a.out/'air/slice0.metallib')]:
    run('execute-'+label,[a.out/'metal_copy_probe',lib,a.out/(label+'.bgra')],deadline=30)
llvm=shutil.which('llvm-dis') or '/opt/homebrew/opt/llvm/bin/llvm-dis'
run('disassemble-air',[llvm,a.out/'air/read_write_surf_compute.air','-o',a.out/'read_write_surf_compute.ll'])
files=list(root.glob('*.*'))+list(a.libraries.glob('*'))+[a.out/'metal_library_probe',a.out/'metal_copy_probe']
(a.out/'hashes.json').write_text(json.dumps({str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files if f.is_file()},indent=2)+'\n')
print('PASS: guest AIR execution and host IOSurface bytes verified; inspect inventories for skipped/unsupported cases.')
