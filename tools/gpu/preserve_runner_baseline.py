#!/usr/bin/env python3
"""Preserve a verified runtime-loader test package without copying its durable base.

Requires two distinct, independently verified Data-loaded revisions and an
uninstrumented successful runner trial. The output retains a byte-identical
installed overlay and its already durable backing files, never a tested VM's
mutable disk. It is for disposable development runners, not the default VM.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import sha256, verify_backing_chain, qcow2_backing_chain
from verify_runner_job import verify


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'installation', 'trial', 'build', 'out'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    m = json.loads(a.manifest.read_text())
    r = json.loads((a.trial/'result.json').read_text())
    if not r.get('passed') or r.get('source_manifest_sha256') != sha256(a.manifest):
        p.error('requires passed trial of this manifest')
    if '-plugin' in m['qemu_argv'] or r.get('debugger'):
        p.error('requires uninstrumented execution')
    revisions = {}
    for job in sorted((a.trial/'runner-jobs').iterdir()):
        desc = json.loads((job/'job.json').read_text())
        result = json.loads((job/'result.json').read_text())
        if result.get('verified'):
            evidence = verify(job)
            if desc.get('mode') == 'data' and desc.get('development'):
                revisions[desc['sha256']] = evidence
    if len(revisions) < 2:
        p.error('requires two distinct verified runtime revisions')
    installation = json.loads(a.installation.read_text())
    installed = installation['guest_installation']
    if Path(installed['build']).resolve() != a.build.resolve() or installation['disk'] != m['disk']:
        p.error('installed build/disk provenance mismatch')
    for name, digest in installed['inputs'].items():
        if sha256(Path(name)) != digest:
            p.error('changed installed input: '+name)
    verify_backing_chain(m['disk']['backing_chain'])
    durable = (Path.home()/'dvm-artifacts').resolve()
    for item in m['disk']['backing_chain'][1:]:
        if not Path(item['path']).resolve().is_relative_to(durable):
            p.error('backing file is not already durable')
    source_disk = Path(m['disk']['path'])
    if source_disk.stat().st_mode & 0o222:
        p.error('installed source overlay must be read-only')
    for name, entry in m['qemu_inputs'].items():
        if sha256(Path(name)) != entry['sha256']:
            p.error('changed pinned boot input: '+name)
    a.out.mkdir(parents=True, exist_ok=False)
    a.out = a.out.resolve()
    disk = a.out/'installed.qcow2'
    shutil.copy2(source_disk, disk)
    if sha256(disk) != m['disk']['backing_chain'][0]['sha256']:
        raise ValueError('overlay copy changed')
    qimg = Path(__file__).resolve().parents[2]/'qemu-sptm/build/qemu-img'
    chain = qcow2_backing_chain(qimg, disk)
    if chain[1:] != m['disk']['backing_chain'][1:]:
        raise ValueError('copied overlay backing chain changed')
    argv = m['qemu_argv']; inputs = {}
    for i, name in [(0, 'qemu-system-aarch64')]+[(argv.index(f)+1, f[1:]) for f in
            ('-bootkc', '-dtree', '-tc', '-ramdisk', '-sptm', '-txm')]:
        dst = a.out/name; shutil.copy2(argv[i], dst)
        dst.chmod(0o555 if i == 0 else 0o444)
        argv[i] = str(dst)
        inputs[str(dst)] = dict(bytes=dst.stat().st_size, sha256=sha256(dst))
    argv[argv.index('-drive')+1] = f'if=none,id=ans,file={disk},format=qcow2'
    shutil.copytree(a.build, a.out/'driver-build')
    env = {k:v for k,v in m['qemu_env'].items() if k not in
           ('DARWIN_TOUCH_EVENTS', 'DARWIN_INPUT_STATUS', 'DARWIN_DCP_GPU_PRESENT_DIR')}
    result = dict(format=m['format'], qemu_argv=argv, qemu_env=env,
                  qemu_inputs=inputs, disk=dict(path=str(disk), backing_chain=chain))
    (a.out/'control.json').write_text(json.dumps(result, indent=2)+'\n')
    (a.out/'provenance.json').write_text(json.dumps(dict(
        source_manifest=str(a.manifest), manifest_sha256=sha256(a.manifest),
        installation=str(a.installation), installation_sha256=sha256(a.installation),
        trial=str(a.trial), result_sha256=sha256(a.trial/'result.json'),
        runtime_revisions=revisions,
        scope='immutable development-loader package; durable backing retained; no checkpoint support'), indent=2)+'\n')
    print(a.out)


if __name__ == '__main__':
    main()
