#!/usr/bin/env python3
"""Pin a stopped, installed disk child for repeatable warm disk boots.

Retains the source machine/CPU configuration, drops saved RAM metadata,
and hashes the new disk chain. Never edits the source manifest or disk.
The caller must stop its staging VM before invoking this tool.
"""
import argparse
import json
from pathlib import Path
import shutil
import time

from checkpoint_common import atomic_json, qcow2_backing_chain, sha256


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('disk', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--tc', type=Path)
    p.add_argument('--qemu', type=Path)
    p.add_argument('--bootkc', type=Path)
    p.add_argument('--dtree', type=Path)
    p.add_argument('--model-env', action='append', default=[], metavar='KEY=VALUE')
    a = p.parse_args()
    if a.output.exists():
        p.error('output must be new')
    source = json.loads(a.source.read_text())
    # RAM checkpoint witnesses describe the source run, never this unbooted
    # disk candidate. Retain provenance through source_manifest instead.
    result = {k: source[k] for k in ('qemu_argv', 'qemu_inputs', 'qemu_env')}
    for key in ('guest_installation', 'battery_source', 'driver_smc_migration',
                'cellular_service_installation', 'tcg_comparison'):
        if key in source:
            result[key] = source[key]
    result.update(format='darwin-vm-warm-disk-v1', created_unix=time.time(),
                  source_manifest=str(a.source.resolve()))
    result['disk'] = dict(path=str(a.disk.resolve()),
        backing_chain=qcow2_backing_chain(Path(shutil.which('qemu-img')), a.disk))
    argv = result['qemu_argv']
    argv[argv.index('-drive')+1] = f'if=none,id=ans,file={a.disk.resolve()},format=qcow2'
    for option, path in [(None, a.qemu), ('-tc', a.tc), ('-bootkc', a.bootkc), ('-dtree', a.dtree)]:
        if path:
            index = 0 if option is None else argv.index(option)+1
            old = argv[index]
            for key in list(result['qemu_inputs']):
                if Path(key).resolve() == Path(old).resolve():
                    del result['qemu_inputs'][key]
            argv[index] = str(path.resolve())
            result['qemu_inputs'][str(path.resolve())] = dict(sha256=sha256(path), bytes=path.stat().st_size)
    for entry in a.model_env:
        key, separator, value = entry.partition('=')
        if not separator or not key.startswith(('DARWIN_', 'GXFSTAT_')):
            p.error('model environment overrides require DARWIN_* or GXFSTAT_* KEY=VALUE')
        result['qemu_env'][key] = value
    atomic_json(a.output, result)
    print(a.output)


if __name__ == '__main__':
    main()
