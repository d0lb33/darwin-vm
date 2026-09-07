#!/usr/bin/env python3
"""Build a read-only QEMU plugin and pin it in an isolated diagnostic manifest."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest', type=Path)
    p.add_argument('out', type=Path)
    a = p.parse_args()
    a.out = a.out.resolve()
    m = json.loads(a.manifest.read_text())
    txm = Path(m['qemu_argv'][m['qemu_argv'].index('-txm') + 1])
    if hashlib.sha256(txm.read_bytes()).hexdigest() != 'b8617cfca055a03711247ad9652f3cfdb026889dcca2eb1436496df4cb61a398':
        raise ValueError('trace addresses require exact 24A5430a TXM')
    if '-plugin' in m['qemu_argv']:
        raise ValueError('refuse an already instrumented manifest')
    root = Path(__file__).resolve().parents[2]
    source = Path(__file__).with_name('txm_signature_trace.c')
    a.out.mkdir(exist_ok=False)
    shutil.copy2(source, a.out/source.name)
    shutil.copy2(__file__, a.out/Path(__file__).name)
    library = a.out/'txm_signature_trace.dylib'
    flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'glib-2.0'], text=True))
    command = ['clang', '-dynamiclib', '-undefined', 'dynamic_lookup', '-Wall', '-Wextra',
               '-Wno-unused-parameter', '-O2', '-I', str(root/'qemu-sptm/include/plugins'),
               str(source), *flags, '-o', str(library)]
    start = time.monotonic()
    subprocess.run(command, check=True)
    undefined = subprocess.check_output(['nm', '-u', str(library)], text=True)
    if any(x in undefined for x in ('qemu_plugin_write_', 'qemu_plugin_set_pc')):
        raise ValueError('trace must not import guest mutation APIs')
    m['qemu_argv'] += ['-plugin', str(library)+',output='+str(a.out/'signature.jsonl')]
    m['qemu_inputs'][str(library)] = dict(bytes=library.stat().st_size,
        sha256=hashlib.sha256(library.read_bytes()).hexdigest())
    record = dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  command=command, build_seconds=time.monotonic()-start,
                  undefined_symbols=undefined, read_only=True,
                  scope='diagnostic branch/register observation; not performance evidence')
    m['diagnostic_plugin'] = record
    (a.out/'build.json').write_text(json.dumps(record, indent=2)+'\n')
    (a.out/'state.json').write_text(json.dumps(m, indent=2)+'\n')
    print(a.out/'state.json')


if __name__ == '__main__':
    main()
