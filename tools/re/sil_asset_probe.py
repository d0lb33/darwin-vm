"""Checkpoint-only SILManager asset-path experiment for iOS 24A5430a.

Supply unchanged firmware assets via guest_file_provision first. Production
assembly must install them at the original ExclaveOS path. This probe changes
only two directory strings and invalidates a previously null manifest cache;
it does not fabricate a manifest, indicator ID, image, or success result.
"""
import json
from pathlib import Path
import lldb

PREFIX = '/private/preboot/Cryptexes/ExclaveOS/System/ExclaveKit/System/Library/'
PATHS = ((0x29d95da40, PREFIX + 'PrivateFrameworks/SILManagerAssets.framework/'),
         (0x29d95dac0, PREFIX + 'Frameworks/SILManagerComponent.framework/secureindicatorassets/'))


def redirect(debugger, slide, directory, report):
    process = debugger.GetSelectedTarget().GetProcess()
    error = lldb.SBError()
    records = []
    once, manifest = slide + 0x2ce831560, slide + 0x2ce831590
    assert process.ReadMemory(manifest, 8, error) == bytes(8), 'manifest already exists'
    state = process.ReadMemory(once, 8, error)
    assert state == b'\xff' * 8, ('manifest initialization state', state.hex())
    for static, original in PATHS:
        address = static + slide
        before = process.ReadMemory(address, len(original) + 1, error)
        assert before == original.encode() + b'\0', (hex(address), before)
        assert len(directory) < len(original)
        # Preserve Swift's encoded string length; extra trailing separators
        # identify the same actual directory and Foundation normalizes them.
        after = directory.encode().ljust(len(original), b'/') + b'\0'
        assert process.WriteMemory(address, after, error) == len(after), str(error)
        assert process.ReadMemory(address, len(after), error) == after
        records.append({'address': hex(address), 'original': original,
                        'replacement': after[:-1].decode()})
    Path(report).write_text(json.dumps(records, indent=2))
    assert process.WriteMemory(once, bytes(8), error) == 8
    records.append({'reset_failed_manifest_once': hex(once), 'manifest': hex(manifest)})
    Path(report).write_text(json.dumps(records, indent=2))
    print('SIL_ASSET_PATH_REDIRECT ' + json.dumps(records), flush=True)
