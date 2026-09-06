"""Read native percentage and libnotify state in a disposable RAM restore.

24A5430a exports and literal are verified against its shared cache. The
probe borrows 16 bytes at a SpringBoard main Mach boundary and restores
them and the complete register set after cancelling its notification token.
By default it does not publish a percentage. An explicit diagnostic_state
argument enables a one-shot native notification-state publication in the
disposable restore; no source dictionary or executable code is changed.
"""
import json
import lldb
import power_iops_liveness_callbacks as native
import welcome_abort_callbacks as names

SLIDE = 0
PATH = None
CALL = None
DONE = False
DIAGNOSTIC_STATE = None
TARGET_PROCESS = 'SpringBoard'
ENTRIES = dict(percent=0x18f0092e0, register=0x2c2169fdc,
               state=0x2c21697d0, cancel=0x2c216c1b0,
               set=0x2c2168eac, post=0x2c21686b8)
LITERAL = 0x18f092b2c

def emit(kind, **fields):
    row = dict(kind=kind, **fields)
    print('POWER_PERCENT ' + json.dumps(row), flush=True)
    with open(PATH, 'a') as f:
        f.write(json.dumps(row) + '\n')

def read(process, address, size):
    error = lldb.SBError()
    raw = process.ReadMemory(address, size, error)
    if error.Fail() or len(raw) != size:
        raise RuntimeError(str(error))
    return raw

def invoke(frame, phase, *args):
    CALL['phase'] = phase
    for i, value in enumerate(args):
        native._set(frame, 'x%d' % i, value)
    native._set(frame, 'lr', CALL['pc'])
    native._set(frame, 'pc', ENTRIES[phase] + SLIDE)

def returned(frame, location, _dict):
    global CALL, DONE
    if CALL is None or native._reg(frame, 'sp') != CALL['sp'] or native._reg(frame, 'tpidr_el1') != CALL['tp']:
        return False
    process = frame.GetThread().GetProcess()
    result = native._reg(frame, 'x0') & 0xffffffff
    raw = read(process, CALL['sp'], 16)
    phase = CALL['phase']
    emit(phase, result=hex(result), scratch=raw.hex())
    if phase == 'percent':
        emit('percent-values', valid=result == 0,
             percent=int.from_bytes(raw[:4], 'little', signed=True),
             charging=raw[4] if result == 0 else None,
             fully_charged=raw[5] if result == 0 else None)
        if CALL.get('published'):
            invoke(frame, 'cancel', CALL['token'])
            return False
        invoke(frame, 'register', LITERAL + SLIDE, CALL['sp'])
        return False
    if phase == 'register' and result == 0:
        CALL['token'] = int.from_bytes(raw[:4], 'little')
        invoke(frame, 'state', CALL['token'], CALL['sp'] + 8)
        return False
    if phase == 'state':
        emit('notification-state', value=hex(int.from_bytes(raw[8:16], 'little')))
        if result == 0 and DIAGNOSTIC_STATE is not None:
            invoke(frame, 'set', CALL['token'], DIAGNOSTIC_STATE)
            return False
        invoke(frame, 'cancel', CALL['token'])
        return False
    if phase == 'set':
        if result != 0:
            invoke(frame, 'cancel', CALL['token'])
        else:
            invoke(frame, 'post', LITERAL + SLIDE)
        return False
    if phase == 'post':
        CALL['published'] = True
        invoke(frame, 'percent', CALL['sp'], CALL['sp'] + 4, CALL['sp'] + 5)
        return False
    error = lldb.SBError()
    if process.WriteMemory(CALL['sp'], CALL['scratch'], error) != 16 or error.Fail():
        raise RuntimeError('cannot restore scratch')
    native._restore(frame, CALL['saved'])
    location.GetBreakpoint().SetEnabled(False)
    CALL, DONE = None, True
    emit('restored')
    return True

def boundary(frame, location, _dict):
    global CALL
    if CALL or DONE or names._progname(frame.GetThread().GetProcess()) != TARGET_PROCESS:
        return False
    sp = native._reg(frame, 'sp')
    if not 0x160000000 <= sp < 0x180000000:
        return False
    process = frame.GetThread().GetProcess()
    expected = b'com.apple.system.powersources.percent\0'
    assert read(process, LITERAL + SLIDE, len(expected)) == expected
    CALL = dict(sp=sp, pc=native._reg(frame, 'pc'), tp=native._reg(frame, 'tpidr_el1'),
                saved=native._save(frame), scratch=read(process, sp, 16))
    bp = process.GetTarget().BreakpointCreateByAddress(CALL['pc'])
    bp.SetScriptCallbackFunction(__name__ + '.returned')
    location.GetBreakpoint().SetEnabled(False)
    invoke(frame, 'percent', sp, sp + 4, sp + 5)
    emit('started')
    return False

def install(debugger, slide, path, cancel_address, diagnostic_state=None,
            target_process='SpringBoard'):
    global SLIDE, PATH, CALL, DONE, DIAGNOSTIC_STATE, TARGET_PROCESS
    SLIDE, PATH, CALL, DONE = int(slide), str(path), None, False
    ENTRIES['cancel'] = int(cancel_address)
    DIAGNOSTIC_STATE = diagnostic_state
    TARGET_PROCESS = target_process
    names.SLIDE[0] = SLIDE
    bp = debugger.GetSelectedTarget().BreakpointCreateByAddress(native.MACH_MSG2_TRAP + SLIDE)
    bp.SetScriptCallbackFunction(__name__ + '.boundary')
    emit('armed', entries={k:hex(v + SLIDE) for k,v in ENTRIES.items()})
