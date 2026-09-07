#!/usr/bin/env python3
"""Assemble a pinned build tree: a validated driver snapshot plus this branch.

The restart experiment must not be confounded by unfinished GPU functionality.
Every guest source comes from the durable snapshot of a previously validated
package unless this branch deliberately changed or added it; those files come
from the worktree and are listed explicitly in the manifest. The result is a
self-contained tree that `build_system_bootstrap.py` and
`prepare_system_bootstrap.py` can be run from unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

SUFFIXES = ('.m', '.h', '.inc', '.py')
# The overlay is measured against this branch's root commit, not HEAD, so a
# later commit cannot silently move guest sources into the pinned tree.
BASE_COMMIT = 'ec9d38a83dfd36791cbb4f83ad3ebfbc41cd6de5'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def branch_changes(repo, base):
    """Files under tools/gpu this branch changed or added relative to `base`."""
    tracked = subprocess.check_output(
        ['git', '-C', str(repo), 'diff', '--name-only', base, '--', 'tools/gpu'], text=True).split()
    untracked = subprocess.check_output(
        ['git', '-C', str(repo), 'ls-files', '--others', '--exclude-standard', '--', 'tools/gpu'],
        text=True).split()
    return {Path(name).name for name in tracked + untracked}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshot', type=Path, help='durable build directory holding the validated tools/gpu sources')
    p.add_argument('out', type=Path)
    p.add_argument('--base', default=BASE_COMMIT,
                   help='commit the branch overlay is measured against; pinned by default')
    a = p.parse_args()
    repo = Path(__file__).resolve().parents[2]
    snapshot = a.snapshot.resolve()
    out = a.out.resolve()
    if not (snapshot/'driver_guest.m').is_file() or not (snapshot/'build.json').is_file():
        p.error('snapshot must be a build_system_bootstrap.py output directory')
    out.mkdir(parents=True, exist_ok=False)
    overlay = branch_changes(repo, a.base)
    shutil.copytree(repo/'tools', out/'tools', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copyfile(repo/'build_tc.py', out/'build_tc.py')
    for link in ('firmware', 'qemu-sptm'):
        (out/link).symlink_to((repo/link).resolve())
    target = out/'tools/gpu'
    pinned, branch, missing = [], [], []
    for name in sorted(x.name for x in snapshot.iterdir() if x.suffix in SUFFIXES):
        if name in overlay:
            continue
        shutil.copyfile(snapshot/name, target/name)
        pinned.append(name)
    for name in sorted(overlay):
        source = repo/'tools/gpu'/name
        if not source.is_file():
            missing.append(name)
            continue
        if source.suffix in SUFFIXES:
            branch.append(name)
    if missing:
        raise SystemExit('branch overlay names files that no longer exist: ' + ', '.join(missing))
    unchanged = [n for n in pinned if sha(snapshot/n) != sha(target/n)]
    if unchanged:
        raise SystemExit('pinned copy mismatch: ' + ', '.join(unchanged))
    manifest = dict(
        scope='validated driver snapshot with this branch\'s session-reload overlay; guest frontend is not this branch\'s unfinished work',
        snapshot=str(snapshot), snapshot_build_sha256=sha(snapshot/'build.json'),
        repo=str(repo), base=subprocess.check_output(['git', '-C', str(repo), 'rev-parse', a.base], text=True).strip(),
        pinned_from_snapshot={n: sha(target/n) for n in pinned},
        overlaid_from_branch={n: sha(repo/'tools/gpu'/n) for n in branch})
    (out/'revision-tree.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(dict(tree=str(out), pinned=len(pinned), overlaid=len(branch)), indent=2))


if __name__ == '__main__':
    main()
