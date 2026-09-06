#!/usr/bin/env python3
"""Pin an isolated QEMU/DT experiment, retaining the original disk and firmware."""
import argparse
import json
from pathlib import Path
import sys
import struct
from aux_namespace_dt import properties, EXPECTED
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import sha256, verify_backing_chain


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest', type=Path)
    p.add_argument('qemu', type=Path)
    p.add_argument('dtree', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    m = json.loads(a.manifest.read_text())
    nodes=properties(a.dtree.read_bytes())
    values=[v[2] for k,v in nodes.items() if k[0].endswith('/arm-io/ans') and k[1]=='namespaces']
    if len(values)!=1 or list(struct.iter_unpack('<III',values[0]))!=EXPECTED+[(8,6,0)]:
        raise ValueError('DT does not contain the exact auxiliary namespace extension')
    verify_backing_chain(m['disk']['backing_chain'])
    for name, record in m['qemu_inputs'].items():
        if sha256(Path(name)) != record['sha256']:
            raise ValueError(f'changed pinned input: {name}')
    argv = m['qemu_argv']
    for index, path in ((0, a.qemu), (argv.index('-dtree') + 1, a.dtree)):
        old = Path(argv[index]).resolve()
        for name in list(m['qemu_inputs']):
            if Path(name).resolve() == old:
                del m['qemu_inputs'][name]
        path = path.resolve()
        argv[index] = str(path)
        m['qemu_inputs'][str(path)] = dict(bytes=path.stat().st_size, sha256=sha256(path))
    m['aux_experiment_source_manifest'] = str(a.manifest.resolve())
    m['aux_namespace_extension'] = [8,6,0]
    with a.output.open('x') as f:
        json.dump(m, f, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
