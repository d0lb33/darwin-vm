#!/usr/bin/env python3
"""Split macOS sample guest-execution observations by deepest named QEMU helper.

Not CPU-time percentages: waits and sampling bias remain. CPU0-3 are the active
set observed in this migration workload; every vCPU remains in per_cpu output.
"""
import argparse,json,re
from collections import Counter
from pathlib import Path
import smp_storage_report as report
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('sample',type=Path)
a=p.parse_args();original=report.classify

def classify(path):
    category=original(path)
    if category!='other_guest_execution_and_helpers':return category
    names=' '.join(path)
    helpers=re.findall(r'\b(helper_[a-zA-Z0-9_]+)',names)
    if helpers:return helpers[-1]
    if re.search(r'\b(?:float(?:16|32|64)|parts(?:64|128))_',names):return 'softfloat_other'
    return 'translated_execution_other'
report.classify=classify
per_cpu=report.sample_tree(a.sample);counts=Counter();total=0
for cpu,record in per_cpu.items():
    if int(cpu)<4:counts.update(record['categories']);total+=record['observations']
print(json.dumps(dict(active_cpu_observations=total,
 categories=[dict(name=k,count=v,percent=100*v/total) for k,v in counts.most_common()],
 per_cpu=per_cpu),indent=2))
