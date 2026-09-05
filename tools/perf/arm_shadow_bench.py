#!/usr/bin/env python3
"""Build and validate three native EL2 shadow-register approaches on this Mac.

No iOS image, production code patch or host privilege changes. Requires
Hypervisor.framework nested virtualization. VMM transition measurements omit
real kernel emulation and page-table synchronization and are lower bounds.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
from arm_island_bench import assemble, ROOT


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--repeat',type=int,default=5)
    p.add_argument('--iterations',type=int,default=100000)
    a=p.parse_args()
    if not 1<=a.repeat<=20 or not 1<=a.iterations<=1000000:p.error('invalid count')
    a.out.mkdir(exist_ok=False)
    exe=a.out/'arm_shadow_bench'
    subprocess.run(['clang','-O2','-Wall','-Wextra','-framework','Hypervisor',str(Path(__file__).with_suffix('.c')),'-o',str(exe)],check=True)
    subprocess.run(['codesign','-s','-','--entitlements',str(ROOT/'hvf-probe/hvf.entitlements'),'-f',str(exe)],check=True)
    for mode in range(4):
        folder=a.out/f'mode{mode}';folder.mkdir()
        (folder/'code.bin').write_bytes(assemble(folder,Path(__file__).with_name('arm_shadow.S'),[f'-DMODE={mode}']))
    labels={(0,0):'guest_el2_undef_handler',(1,0):'vmm_exit',(1,1):'vmm_exit_gpr_simd_roundtrip',(2,0):'preserving_code_thunks',(3,0):'guest_el1_udf_control'}
    report={'runs':[]}
    for rep in range(a.repeat):
        order=list(labels) if rep%2==0 else list(reversed(labels))
        for mode,full in order:
            result=subprocess.run([str(exe),str(a.out/f'mode{mode}/code.bin'),str(mode),str(a.iterations),str(full)],capture_output=True,text=True,timeout=18)
            (a.out/f'{rep}_{mode}_{full}.log').write_text(result.stdout+result.stderr)
            if result.returncode:raise RuntimeError(f'{mode}/{full} failed {result.returncode}: {result.stdout} {result.stderr}')
            row=json.loads(result.stdout);row.update(label=labels[mode,full],repeat=rep);report['runs'].append(row)
            print(json.dumps(row),flush=True)
            (a.out/'results.json').write_text(json.dumps(report,indent=2))
    report['median_ns_per_access']={label:statistics.median(r['ns_per_access'] for r in report['runs'] if r['label']==label) for label in labels.values()}
    (a.out/'results.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report['median_ns_per_access'],indent=2))


if __name__=='__main__':main()
