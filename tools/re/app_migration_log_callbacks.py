"""Read-only 24A5430a DataMigration log observer for first-install diagnosis.

__DMLogFunc entry is 0x25e8ef02c in DataMigration; its caller passes the
format NSString in x2 and arm64 variadic values on the entry stack. Verified
against SystemAppMigrator 0x7080..0x708c and 0x70xx parameter logging.
Install with a freshly measured shared-cache slide. This never evaluates an
Objective-C expression, writes guest state, or extends migration watchdogs.
"""
import json
import time

import bks_checkin_callbacks as bks

LOG_SITE = 0x25e8ef02c
PATH = None
HITS = 0
ALL_HITS = 0
LIMIT = 2000
STOP_ON_FAILURE = True
STARTUP_LIMIT = 2000


def describe(process, address):
    """Decode only the measured constant/inline CFString forms; keep raw values."""
    if not address:
        return None
    raw = bks._read(process, address, 32)
    if len(raw) != 32:
        return '<unreadable>'
    flags = int.from_bytes(raw[8:10], 'little')
    isa = int.from_bytes(raw[:8], 'little') & 0x7fffffffff8
    # NSPathStore2: measured HealthENLauncher.app and Vehicle.app headers;
    # cache symbol identifies the class, +8 upper bits count UTF-16 at +12.
    if isa == 0x1e6f2d408 + bks.SLIDE[0]:
        count = int.from_bytes(raw[8:12], 'little') >> 20
        if count > 1024:
            return '<oversize NSPathStore2>'
        value = bks._read(process, address + 12, count * 2)
        return value.decode('utf-16-le', 'replace') if len(value) == count * 2 else '<unreadable>'
    if flags == 0x7c8:
        pointer = int.from_bytes(raw[16:24], 'little')
        size = int.from_bytes(raw[24:32], 'little')
    elif flags == 0x78c:
        pointer, size = address + 17, raw[16]
    else:
        return '<object flags=0x%x>' % flags
    if size > 2048:
        return '<oversize>'
    text = bks._read(process, pointer, size)
    return text.decode('utf-8', 'replace') if len(text) == size else '<unreadable>'


def on_log(frame, location, _dict):
    global HITS, ALL_HITS
    process = frame.GetThread().GetProcess()
    name = bks._progname(process)
    ALL_HITS += 1
    fmt = describe(process, bks._reg(frame, 'x2'))
    if ALL_HITS <= STARTUP_LIMIT:
        details = {}
        if fmt and any(s in fmt for s in ('Will create migrator', 'Found %@ items', 'Gathered %@', 'Error instantiating', 'lastRelevantPlugin')):
            raw = bks._read(process, bks._reg(frame, 'sp'), 32)
            values = [int.from_bytes(raw[i:i+8], 'little') for i in range(0, len(raw), 8)]
            details['arguments'] = [dict(raw=hex(v), string=describe(process, v)) for v in values]
        with open(PATH + '.startup', 'a') as stream:
            stream.write(json.dumps(dict(time=time.time(), process=name, format=fmt,
                format_pointer=hex(bks._reg(frame, 'x2')),
                prologue=bks._read(process, LOG_SITE + bks.SLIDE[0], 20).hex() if ALL_HITS == 1 else None,
                **details)) + '\n')
    if name not in ('com.apple.migrationpluginwrapper', 'com.apple.datamigrator'):
        return False
    if not fmt or not fmt.startswith('MISystemAppMigrator:'):
        return False
    HITS += 1
    stack = bks._read(process, bks._reg(frame, 'sp'), 8 * 8)
    values = [int.from_bytes(stack[i:i+8], 'little') for i in range(0, len(stack), 8)]
    record = dict(time=time.time(), process=name, hit=HITS,
                  pc=hex(bks._reg(frame, 'pc')), caller=hex(bks._reg(frame, 'x0')),
                  level=bks._reg(frame, 'x1'), format=fmt,
                  arguments=[dict(raw=hex(value), string=describe(process, value)) for value in values])
    with open(PATH, 'a') as stream:
        stream.write(json.dumps(record, sort_keys=True) + '\n')
    print('APP_MIGRATION_LOG ' + json.dumps(record, sort_keys=True), flush=True)
    if HITS >= LIMIT:
        location.GetBreakpoint().SetEnabled(False)
    return STOP_ON_FAILURE and any(token in fmt for token in (
        'installation was cancelled', 'Failed to create AppInstallCoordinator',
        'Timed out after waiting', 'Timed out waiting for all apps',
        'should be installed, but is actually NOT installed',
        'does not match the version in the trust cache'))


def install(debugger, slide, path):
    global PATH, HITS
    PATH, HITS = path, 0
    bks.SLIDE[0] = slide
    breakpoint = debugger.GetSelectedTarget().BreakpointCreateByAddress(LOG_SITE + slide)
    breakpoint.SetScriptCallbackFunction('app_migration_log_callbacks.on_log')
    print('APP_MIGRATION_LOG_READY id=%d address=0x%x path=%s' %
          (breakpoint.GetID(), LOG_SITE + slide, path), flush=True)
    return breakpoint.GetID()


def on_bundle(frame, location, _dict):
    """Count native LS bundle scans; MIBundle name +48 is from ObjC ivars."""
    process = frame.GetThread().GetProcess()
    if bks._progname(process) != 'lsd':
        return False
    is_validate = bks._reg(frame, 'pc') - bks.SLIDE[0] == 0x1aae92d48
    bundle = bks._reg(frame, 'x0' if is_validate else 'x2')
    name = bks._u64(process, bundle + 48)
    record = dict(time=time.time(), phase='validate' if is_validate else 'scan', bundle=hex(bundle), name=describe(process, name),
                  name_pointer=hex(name or 0), name_bytes=bks._read(process, name, 64).hex() if name else None,
                  return_address=hex(bks._reg(frame, 'lr') & 0xffffffffffff))
    with open(PATH + '.bundles', 'a') as stream:
        stream.write(json.dumps(record) + '\n')
    if location.GetHitCount() >= 5000:
        location.GetBreakpoint().SetEnabled(False)
    return False


def install_bundle_observer(debugger):
    for address in (0x1aaedb598, 0x1aae92d48):
        bp = debugger.GetSelectedTarget().BreakpointCreateByAddress(address + bks.SLIDE[0])
        bp.SetScriptCallbackFunction('app_migration_log_callbacks.on_bundle')
        print('BUNDLE_SCAN_OBSERVER_READY id=%d' % bp.GetID(), flush=True)
