"""One-time 24A5430a Home transition for a migrated development image.

Requires home_display_callbacks' explicit UI-auth diagnostics. Native method
evidence is in docs/re/setup-skip-runtime.md. No per-touch debugger callback;
preserve registers through touch_bridge and disable this observer on return.
"""
import lldb
import touch_bridge as calls
import welcome_abort_callbacks as names

STARTED = False
FINISH_UNLOCK = False
UNBLANK_SCREEN = False
OPEN_SETUP = False
RETURN_HOME = True


def steps(frame):
    p = frame.GetThread().GetProcess()
    slide = calls.SLIDE
    sb = calls.u64(p, 0x270951218 + slide)
    offset = int.from_bytes(calls.read(p, 0x26cf35748 + slide, 4), 'little')
    idle = calls.u64(p, sb + offset)
    if not sb or not idle:
        raise RuntimeError('missing SpringBoard/idle coordinator')
    def unlock(f, s):
        manager = s['results'][2]
        if not manager:
            raise RuntimeError('missing main-display lock manager')
        return 0x224c4cc00, dict(x0=manager, x2=1, x3=0)
    result = [
        (0x22487b064, dict(x0=idle, x2=0x2732af6a8 + slide)),
        lambda f, s: (0x18040526c, dict(x0=s['results'][0])),
    ]
    if FINISH_UNLOCK:
        result += [(0x224c459f0, dict(x0=0x2709464d0 + slide)), unlock]
    if UNBLANK_SCREEN:
        # BKSDisplayServicesSetScreenBlanked(false), 24A5430a:
        # 0x18a1330e8 preserves x0, 0x18a133138 passes it as blanked to
        # _BKSDisplayNotifySetDisplayBlanked. Native server/permission path.
        result += [(0x18a1330d4, dict(x0=0))]
    if OPEN_SETUP:
        def application_result(f, s, address):
            obj = s['results'][-1]
            if not obj:
                raise RuntimeError('missing native Setup application/controller')
            return address, dict(x0=obj)
        # SpringBoard applicationController: 0x224287cd8 (ivar +0x614).
        # setupApplication: 0x2243bf920 resolves SBBuddyBundleIdentifier.
        # Native caller 0x2242e4a58..64 passes that result as the sole argument
        # to _SBWorkspaceActivateApplication (0x22433ff18).
        result += [
            (0x224287cd8, dict(x0=sb)),
            lambda f, s: application_result(f, s, 0x2243bf920),
            lambda f, s: application_result(f, s, 0x22433ff18),
        ]
        return result
    if RETURN_HOME:
        result += [(0x2244a949c, dict(x0=sb, x2=0))]
    return result + [(0x2242fa85c, dict(x0=idle, x2=0x2732af6a8 + slide))]


def callback(frame, location, _dict):
    global STARTED
    try:
        if calls.STATE:
            state = calls.STATE
            if (frame.FindRegister('tpidr_el1').GetValueAsUnsigned() != state['tp'] or
                    frame.FindRegister('sp').GetValueAsUnsigned() != state['sp']):
                return False
            state['results'].append(frame.FindRegister('x0').GetValueAsUnsigned())
            state['index'] += 1
            if state['index'] < len(state['steps']):
                calls.advance(frame)
                return False
            for name, raw in state['regs'].items():
                error = lldb.SBError()
                data = lldb.SBData()
                data.SetData(error, raw, lldb.eByteOrderLittle, 8)
                if not frame.FindRegister(name).SetData(data, error) or error.Fail():
                    raise RuntimeError('register restore: ' + name)
            print('NATIVE_HOME_DONE results=' + repr([hex(v) for v in state['results']]), flush=True)
            calls.STATE = None
            location.GetBreakpoint().SetEnabled(False)
            return False
        if STARTED or names._progname(frame.GetThread().GetProcess()) != 'SpringBoard':
            return False
        if not 0x160000000 <= frame.FindRegister('sp').GetValueAsUnsigned() < 0x180000000:
            return False
        STARTED = True
        calls.begin(frame, steps(frame), 'native-home')
        return False
    except Exception as error:
        print('NATIVE_HOME_ERROR ' + str(error), flush=True)
        return True


def install(debugger, slide, finish_unlock=False, unblank=False, setup=False,
            return_home=True):
    global STARTED, FINISH_UNLOCK, UNBLANK_SCREEN, OPEN_SETUP, RETURN_HOME
    if calls.STATE:
        raise RuntimeError('another native call is active')
    STARTED = False
    FINISH_UNLOCK = finish_unlock
    UNBLANK_SCREEN = unblank
    OPEN_SETUP = setup
    RETURN_HOME = return_home
    calls.SLIDE = names.SLIDE[0] = slide
    bp = debugger.GetSelectedTarget().BreakpointCreateByAddress(0x1806544e8 + slide)
    bp.SetScriptCallbackFunction(__name__ + '.callback')
    print('NATIVE_HOME_ARMED breakpoint=%d' % bp.GetID(), flush=True)
