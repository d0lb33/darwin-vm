#!/usr/bin/env python3
"""Verify final nm -u names against arm64e exports in the exact guest cache."""
import argparse
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_CACHE = Path('/Users/jdolbe1/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e')
HEADER = re.compile(r'^(.+) \[arm64e\]:$')
EXPORT = re.compile(r'^\s+0x[0-9A-Fa-f]+\s+(\S+)$')
REEXPORT = re.compile(r'^\s+\[re-export\]\s+(\S+)')


def imports(path: Path) -> set[str]:
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('nm_u', type=Path, nargs='+')
    args = parser.parse_args()
    requested = set().union(*(imports(path) for path in args.nm_u))
    command = ['dyld_info', '-exports', '-all_dyld_cache', str(args.cache)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)
    assert process.stdout is not None
    providers: dict[str, set[str]] = {name: set() for name in requested}
    image = None
    for line in process.stdout:
        header = HEADER.match(line.rstrip('\n'))
        if header:
            image = header.group(1)
            continue
        if image is None:
            continue
        item = EXPORT.match(line) or REEXPORT.match(line)
        if item and item.group(1) in providers:
            providers[item.group(1)].add(image)
    if process.wait() != 0:
        return process.returncode
    rows = ['import\tguest-cache provider(s)']
    missing = []
    for name in sorted(requested):
        found = sorted(providers[name])
        rows.append(f'{name}\t' + (';'.join(found) if found else 'MISSING'))
        if not found:
            missing.append(name)
    args.output.write_text('\n'.join(rows) + '\n')
    if missing:
        print('missing exact guest exports: ' + ', '.join(missing), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
