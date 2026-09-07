#!/usr/bin/env python3
"""Preserve the verified shared CARenderer package with durable boot inputs.

Copies the sealed installation overlay, never a running/test VM's mutable disk.
Retains its already durable backing chain. Does not imply another runtime test.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import sha256, verify_backing_chain, qcow2_backing_chain
from verify_runner_job import verify
from shared_consumer_verify import verify_scanout_export


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'trial', 'job', 'installed-build', 'runtime-build', 'linked', 'out'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--handoff-jobs',nargs='+',type=int,help='also require verified successive-process surface reuse')
    a = p.parse_args()
    m = json.loads(a.manifest.read_text())
    result = json.loads((a.trial/'result.json').read_text())
    if not result.get('passed') or result.get('debugger') or result.get('ram_restored') or result.get('kept_paused'):
        p.error('requires passed, stopped, uninstrumented disk trial')
    if result['source_manifest_sha256'] != sha256(a.manifest):
        p.error('trial manifest changed')
    if a.job.resolve().parent != (a.trial/'runner-jobs').resolve():
        p.error('job does not belong to trial')
    job = json.loads((a.job/'job.json').read_text())
    if not job.get('shared_surface') or job.get('mode') != 'data' or not job.get('development'):
        p.error('requires shared-surface runtime revision')
    evidence = verify(a.job)
    scanout = verify_scanout_export(a.trial, a.job)
    handoff=None
    if a.handoff_jobs:
        from verify_surface_handoff_batch import verify_batch
        if job['job']!=a.handoff_jobs[-1]:p.error('final scanout must match last handoff job')
        handoff=verify_batch(a.trial,a.handoff_jobs)
    if sha256(a.linked/'DVMProxy.bundle/DVMProxy') != job['sha256']:
        p.error('linked revision differs from tested job')
    worker = json.loads((a.job/'worker.json').read_text())
    if sha256(a.runtime_build/'driver_host') != worker['sha256']:
        p.error('runtime backend differs from tested job')
    installed = m['guest_installation']
    if Path(installed['build']).resolve() != a.installed_build.resolve():
        p.error('installed build mismatch')
    for name, digest in installed['inputs'].items():
        if sha256(Path(name)) != digest:
            p.error('installed input changed: '+name)
    verify_backing_chain(m['disk']['backing_chain'])
    durable = (Path.home()/'dvm-artifacts').resolve()
    for item in m['disk']['backing_chain'][1:]:
        if not Path(item['path']).resolve().is_relative_to(durable):
            p.error('backing file is not durable')
    source_disk = Path(m['disk']['path'])
    if source_disk.stat().st_mode & 0o222:
        p.error('installation overlay must be read-only')
    for name, entry in m['qemu_inputs'].items():
        if sha256(Path(name)) != entry['sha256']:
            p.error('pinned input changed: '+name)
    a.out = a.out.resolve()
    a.out.mkdir(parents=True, exist_ok=False)
    disk = a.out/'installed.qcow2'
    shutil.copy2(source_disk, disk)
    qimg = Path(__file__).resolve().parents[2]/'qemu-sptm/build/qemu-img'
    chain = qcow2_backing_chain(qimg, disk)
    if chain[0]['sha256'] != m['disk']['backing_chain'][0]['sha256'] or chain[1:] != m['disk']['backing_chain'][1:]:
        raise ValueError('copied disk or backing chain changed')
    argv = m['qemu_argv'][:]
    inputs = dict(m['qemu_inputs'])
    for i in [0]+[argv.index(f)+1 for f in ('-bootkc','-dtree','-tc','-ramdisk','-sptm','-txm')]:
        src = Path(argv[i]).resolve()
        if not src.is_relative_to(durable):
            dest = a.out/('qemu-system-aarch64' if i == 0 else argv[i-1][1:])
            shutil.copy2(src, dest)
            dest.chmod(0o555 if i == 0 else 0o444)
            inputs[str(dest)] = inputs.pop(argv[i])
            argv[i] = str(dest)
    argv[argv.index('-drive')+1] = f'if=none,id=ans,file={disk},format=qcow2'
    for src, name in ((a.installed_build,'installed-build'), (a.runtime_build,'runtime-build'), (a.linked,'guest-validated-linked')):
        shutil.copytree(src, a.out/name)
    control = dict(format=m['format'], qemu_argv=argv, qemu_env=m['qemu_env'],
                   qemu_inputs=inputs, disk=dict(path=str(disk),backing_chain=chain))
    (a.out/'control.json').write_text(json.dumps(control,indent=2)+'\n')
    shutil.copy2(a.manifest,a.out/'source-manifest.json')
    (a.out/'provenance.json').write_text(json.dumps(dict(
        manifest_sha256=sha256(a.manifest), trial=str(a.trial), result_sha256=sha256(a.trial/'result.json'),
        job=evidence, scanout=scanout, handoff=handoff,
        scope='sealed installation copy; disposable children only; supervisor owns the VM-lifetime pool' if handoff else 'sealed installation copy; disposable children only; one pool-owning process per VM until handoff is proven'),indent=2)+'\n')
    print(a.out)


if __name__ == '__main__':
    main()
