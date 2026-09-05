"""Explicit laboratory-only Setup suppression for the six-core ADFL checkpoint.

No persistent activation claim: this debugger experiment overrides only the
Setup launch predicate and SpringBoard's bricked-device UI predicate. It is
never installed by default. Native addresses are from 24A5430a.
"""
import welcome_abort_callbacks as names

HITS = {}

def on_authenticated(frame, location, _dict):
    """Diagnostic UI-only override; never an emulated keybag unlock."""
    if names._progname(frame.GetThread().GetProcess()) != 'SpringBoard':
        return False
    lr = frame.FindRegister('lr').GetValueAsUnsigned() & 0xffffffffffff
    if not lr:
        return True
    if not frame.FindRegister('x0').SetValueFromCString('1'):
        return True
    if not frame.FindRegister('pc').SetValueFromCString(hex(lr)):
        return True
    key = location.GetBreakpoint().GetID()
    HITS[key] = HITS.get(key, 0) + 1
    if HITS[key] <= 8:
        print('HOME_UI_AUTH_DIAGNOSTIC hit=%d return=0x%x' % (HITS[key], lr), flush=True)
    return False

def install_ui_auth_diagnostic(debugger, slide):
    # SBFUserAuthenticationController isAuthenticated, 24A5430a, native entry.
    # The native finish-UI method asserts this at SpringBoard 0x224c4c6dc.
    for address in (0x1bb27afd8, 0x1bb27cf98):
        # _isUserAuthenticated is also queried directly by lock-status clients.
        # R17 main-thread stack: _isUserAuthenticated -> uncache passcode ->
        # aks_assert_hold, blocked in IOConnectCallMethod (touch notes).
        b = debugger.GetSelectedTarget().BreakpointCreateByAddress(address + slide)
        b.SetScriptCallbackFunction('home_startup_probe.on_authenticated')
        print('HOME_UI_AUTH_DIAGNOSTIC_READY', b.GetID(), flush=True)

def on_false(frame, location, _dict):
    process = frame.GetThread().GetProcess()
    name = names._progname(process)
    key = location.GetBreakpoint().GetID()
    if key == BRICKED and name != 'SpringBoard':
        return False
    lr = frame.FindRegister('lr').GetValueAsUnsigned() & 0xffffffffffff
    if not lr:
        return True
    if not frame.FindRegister('x0').SetValueFromCString('0'):
        return True
    if not frame.FindRegister('pc').SetValueFromCString(hex(lr)):
        return True
    HITS[key] = HITS.get(key, 0) + 1
    if HITS[key] <= 8:
        print('HOME_STARTUP_SUPPRESS process=%s kind=%s hit=%d return=0x%x' %
              (name, 'bricked-ui' if key == BRICKED else 'needs-setup', HITS[key], lr), flush=True)
    return False

def install(debugger, slide):
    global BRICKED
    names.SLIDE[0] = slide
    t = debugger.GetSelectedTarget()
    # Entry precedes PAC prologue, so native LR is the ordinary call return.
    b = t.BreakpointCreateByAddress(0x22458d184 + slide)
    BRICKED = b.GetID()
    b.SetScriptCallbackFunction('home_startup_probe.on_false')
    b = t.BreakpointCreateByAddress(0x1cac11c18 + slide)
    b.SetScriptCallbackFunction('home_startup_probe.on_false')
    print('HOME_STARTUP_SUPPRESS_READY diagnostic-only', flush=True)
