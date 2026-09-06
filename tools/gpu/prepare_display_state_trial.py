#!/usr/bin/env python3
"""Freeze an owned QEMU and prepare paired display-state experiment manifests."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--boot-build', type=Path, required=True)
p.add_argument('--qemu', type=Path, required=True)
p.add_argument('--installed', type=Path, required=True)
p.add_argument('--out', type=Path, required=True)
a = p.parse_args()
a.out.mkdir(exist_ok=False)
for name in ('bootkc', 'system.dtree', 'ledger.json'):
    shutil.copy2(a.boot_build/name, a.out/name)
shutil.copy2(a.qemu, a.out/'qemu-system-aarch64')
qemu_root=Path(subprocess.check_output(['git','-C',str(a.qemu.resolve().parent),
                                      'rev-parse','--show-toplevel'],text=True).strip())
sources={}
for name in ('hw/arm/darwin_iomfb.c','hw/arm/darwin_iomfb_swap.c',
             'include/hw/arm/darwin_iomfb_swap.h','tests/unit/test-darwin-iomfb-swap.c',
             'hw/arm/xnuboot_sptm.c','hw/arm/darwin_gpu_transport.c',
             'include/xnu/darwin_gpu_transport.h'):
    data=(qemu_root/name).read_bytes()
    target=a.out/'qemu-source'/name;target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(data);sources[name]=hashlib.sha256(data).hexdigest()
head=subprocess.check_output(['git','-C',str(qemu_root),'rev-parse','HEAD'],text=True).strip()
diff=subprocess.check_output(['git','-C',str(qemu_root),'diff','HEAD','--',*sources])
(a.out/'qemu-diff.txt').write_bytes(diff)
(a.out/'qemu-source.json').write_text(json.dumps(dict(head=head,sources=sources,
    binary_sha256=hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
    scope='source captured at freeze time; caller must build before freezing'),indent=2)+'\n')
control = a.out/'control.json'
subprocess.run([sys.executable, str(Path(__file__).with_name('prepare_mmio_manifest.py')),
                str(a.installed), str(a.out), str(control)], check=True)
m = json.loads(control.read_text())
m['qemu_env']['DARWIN_DCP_GPU_PRESENT_DIR'] = str(a.out)  # runner rebinds
m['qemu_env']['DARWIN_DCP_IOMFB_DISPLAY_STATE'] = '0'
control.write_text(json.dumps(m, indent=2)+'\n')
m['qemu_env']['DARWIN_DCP_IOMFB_DISPLAY_STATE'] = '1'
(a.out/'state.json').write_text(json.dumps(m, indent=2)+'\n')
print(a.out)
