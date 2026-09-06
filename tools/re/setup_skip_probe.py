"""Explicit debugger-only native UI calls on a checkpoint child.

24A5430a SetupController and shared-storage offsets come from the Setup Mach-O.
No default boot behavior changes. Entry is a native main run-loop observer.
Defaults target Setup; SpringBoard experiments must configure receivers/addresses.
"""
import lldb
import welcome_abort_callbacks as names

SETUP_SLIDE = 0x830000
HIT = None
ANCHOR = None
TARGET_PROCESS = "Setup"
CONTROLLER_ADDRESS = 0x1003c0d90 + SETUP_SLIDE

def on_observer(frame, location, _dict):
    global HIT, ANCHOR
    p = frame.GetThread().GetProcess()
    if names._progname(p) != TARGET_PROCESS:
        return False
    # The UIKit main stack is in the executable stack region, unlike workers.
    sp = frame.GetSP()
    if not 0x160000000 <= sp < 0x180000000:
        return False
    controller = names._u64(p, CONTROLLER_ADDRESS)
    print('SETUP_SKIP_ANCHOR sp=0x%x controller=0x%x tp=0x%x' %
          (sp, controller, frame.FindRegister('tpidr_el1').GetValueAsUnsigned()), flush=True)
    if not controller:
        return True
    HIT = (frame.GetThread().GetThreadID(), controller)
    ANCHOR = (frame.GetPC(), sp, frame.FindRegister('tpidr_el1').GetValueAsUnsigned())
    location.GetBreakpoint().SetEnabled(False)
    return True

def install(debugger, slide):
    names.SLIDE[0] = slide
    bp = debugger.GetSelectedTarget().BreakpointCreateByAddress(0x1806544e8 + slide)
    bp.SetScriptCallbackFunction('setup_skip_probe.on_observer')
    print('SETUP_SKIP_READY', bp.GetID(), flush=True)

CALL = None
STAGE_RECEIVERS = None

def _set(frame, name, value):
    if not frame.FindRegister(name).SetValueFromCString(hex(value)):
        raise RuntimeError('cannot set ' + name)

def start(debugger):
    """Run configured native calls at the freshly captured main observer."""
    global CALL
    if HIT is None or ANCHOR is None or CALL is not None:
        raise RuntimeError('need a fresh main-thread observer anchor')
    target = debugger.GetSelectedTarget()
    frame = target.GetProcess().GetThreadByID(HIT[0]).GetFrameAtIndex(0)
    current = (frame.GetPC(), frame.GetSP(),
               frame.FindRegister('tpidr_el1').GetValueAsUnsigned())
    if current != ANCHOR:
        raise RuntimeError('stale observer anchor; capture again before calling')
    if names._progname(target.GetProcess()) != TARGET_PROCESS:
        raise RuntimeError('current address space is not ' + TARGET_PROCESS)
    registers = {}
    for name in ([f'x{i}' for i in range(29)] + ['fp', 'lr', 'sp', 'pc', 'cpsr'] +
                 [f'v{i}' for i in range(32)] + ['fpsr', 'fpcr']):
        value = frame.FindRegister(name)
        if value.IsValid():
            error = lldb.SBError()
            data = value.GetData().ReadRawData(error, 0, value.GetByteSize())
            if error.Fail():
                raise RuntimeError('cannot save ' + name)
            registers[name] = data
    CALL = {'registers': registers, 'pc': frame.GetPC(), 'sp': frame.GetSP(),
            'tp': frame.FindRegister('tpidr_el1').GetValueAsUnsigned(),
            'controller': HIT[1], 'stage': 0}
    bp = target.BreakpointCreateByAddress(frame.GetPC())
    bp.SetScriptCallbackFunction('setup_skip_probe.on_return')
    CALL['bp'] = bp.GetID()
    _next(frame)

# Native markBuddyComplete saves FinishedInitialRun and synchronizes defaults.
# Native lifecycle cause 1 is the normal home-gesture completion path, compared
# at Setup+0x1f184; allowDismissal is the bool consumed at +0x1f1dc.
STAGES = [(0x100013658, 'markBuddyComplete', 0, 0),
          (0x10001ef8c, 'willEndLifecycleDueToCause:allowDismissal:', 1, 1),
          (0x10001fa78, 'endLifecycleDueToCause:', 1, 0)]

def _next(frame):
    address, label, x2, x3 = STAGES[CALL['stage']]
    receiver = STAGE_RECEIVERS[CALL['stage']] if STAGE_RECEIVERS else CALL['controller']
    if receiver is None:
        receiver = CALL['last_result']
    for name, value in [('x0', receiver), ('x1', 0), ('x2', x2),
                        ('x3', x3), ('lr', CALL['pc']), ('pc', address + SETUP_SLIDE)]:
        _set(frame, name, value)
    print('SETUP_SKIP_NATIVE_CALL stage=%d method=%s address=0x%x' %
          (CALL['stage'], label, address + SETUP_SLIDE), flush=True)

def on_return(frame, location, _dict):
    global HIT, ANCHOR
    if (CALL is None or frame.GetSP() != CALL['sp'] or
            frame.FindRegister('tpidr_el1').GetValueAsUnsigned() != CALL['tp']):
        return False
    print('SETUP_SKIP_NATIVE_RETURN stage=%d x0=0x%x' %
          (CALL['stage'], frame.FindRegister('x0').GetValueAsUnsigned()), flush=True)
    CALL['last_result'] = frame.FindRegister('x0').GetValueAsUnsigned()
    CALL.setdefault('results', []).append(CALL['last_result'])
    CALL['stage'] += 1
    if CALL['stage'] < len(STAGES):
        _next(frame)
        return False
    for name, raw in CALL['registers'].items():
        data = lldb.SBData()
        error = lldb.SBError()
        data.SetData(error, raw, lldb.eByteOrderLittle, 8)
        if error.Fail() or not frame.FindRegister(name).SetData(data, error):
            raise RuntimeError('cannot restore ' + name + ': ' + str(error))
    location.GetBreakpoint().SetEnabled(False)
    # The guest main thread can return on a different emulated CPU.
    HIT = (frame.GetThread().GetThreadID(), CALL['controller'])
    ANCHOR = (CALL['pc'], CALL['sp'], CALL['tp'])
    print('SETUP_SKIP_NATIVE_DONE registers restored; resume native observer', flush=True)
    return True
