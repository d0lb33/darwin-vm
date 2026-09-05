#!/usr/bin/env python3
"""Register the native input service in the development image's launchd cache.

24A5430a loads System/Library/xpc/launchd.plist; adding a loose LaunchDaemons
plist alone did not launch dvm-input. This edits only its LaunchDaemons entry.
The derived cache requires the existing launchd_unsecure_cache=1 boot argument.
Run against copied artifacts, then install inside a disposable restore guest.
"""
import argparse
from pathlib import Path
import plistlib

SERVICE_PATH = '/System/Library/LaunchDaemons/com.apple.dvm-input.plist'


def register(cache, service):
    if not isinstance(cache, dict) or not isinstance(cache.get('LaunchDaemons'), dict):
        raise ValueError('unsupported launchd cache schema')
    if service.get('Label') != 'com.apple.dvm-input':
        raise ValueError('only the native input service is supported')
    if service.get('ProgramArguments') != ['/usr/local/libexec/dvm-input']:
        raise ValueError('unexpected native input executable')
    daemons = cache['LaunchDaemons']
    for path, entry in daemons.items():
        if path != SERVICE_PATH and entry.get('Label') == service['Label']:
            raise ValueError(f'duplicate label in cache: {path}')
    if SERVICE_PATH in daemons and daemons[SERVICE_PATH] != service:
        raise ValueError('refusing to replace a differing cached input service')
    return dict(cache, LaunchDaemons=dict(daemons, **{SERVICE_PATH: service}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve() == args.input.resolve():
        parser.error('output must be a new artifact')
    cache = plistlib.loads(args.input.read_bytes())
    service = plistlib.loads(Path(__file__).with_name('com.apple.dvm-input.plist').read_bytes())
    result = register(cache, service)
    encoded = plistlib.dumps(result, fmt=plistlib.FMT_BINARY, sort_keys=False)
    if plistlib.loads(encoded) != result:
        raise ValueError('cache round-trip verification failed')
    args.output.write_bytes(encoded)
    print(f'{len(cache["LaunchDaemons"])} -> {len(result["LaunchDaemons"])} services; wrote {args.output}')


if __name__ == '__main__':
    main()
