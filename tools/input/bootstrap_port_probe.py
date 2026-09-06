"""Bounded native bootstrap-port query from a paused dvm-input EL0 boundary.

24A5430a libsystem_kernel exports are recorded below. Query through Mach's
permission checks; never copy a port name between IPC namespaces or edit the
kernel's port tables. A failed query is reported and anonymous storage freed.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 're'))
import lldb
import touch_bridge as calls
import welcome_abort_callbacks as names


def callback(frame, location, data):
    if calls.STATE is None:
        location.GetBreakpoint().SetEnabled(False)
        return False
    stop = calls.on_observer(frame, location, data)
    if calls.STATE is None:
        location.GetBreakpoint().SetEnabled(False)
    return stop


def start(debugger, slide, source_pid, expected_process='dvm-input', frame=None, control=False):
    """Caller must first stop the named process at a verified call boundary."""
    if calls.STATE is not None or source_pid <= 1:
        raise RuntimeError('active native call or invalid source PID')
    target = debugger.GetSelectedTarget()
    frame = frame or target.GetProcess().GetSelectedThread().GetFrameAtIndex(0)
    names.SLIDE[0] = calls.SLIDE = slide
    if names._progname(target.GetProcess()) != expected_process or calls.u64(
            target.GetProcess(), 0x26fd74008 + slide) & 0xffffffff == source_pid:
        raise RuntimeError('wrong process context or source PID')
    if frame.FindRegister('cpsr').GetValueAsUnsigned() & 15:
        raise RuntimeError('requires an EL0 native call boundary')

    def base(state):
        address = state['results'][0]
        if not 0x1000 <= address < 1 << 43:
            raise RuntimeError('mmap failed')
        return address

    def self_port(f):
        return calls.u64(f.GetThread().GetProcess(), 0x26fd74078 + slide) & 0xffffffff

    def query(f, s):
        if s['results'][2]:
            return 0x237cd515c, {}  # getpid: harmless skipped-query placeholder
        port = calls.u64(f.GetThread().GetProcess(), base(s)) & 0xffffffff
        return 0x237cd0824, dict(x0=port, x1=4, x2=base(s)+8)

    def cleanup(f, s):
        raw = calls.read(f.GetThread().GetProcess(), base(s), 16)
        print('BOOTSTRAP_PORT_PROBE source_pid=%d control=%d task_result=%#x query=%s ports=%s' %
              (source_pid, control, s['results'][2], hex(s['results'][3]) if not s['results'][2]
               else 'skipped', raw.hex()), flush=True)
        # Retain returned rights for a subsequent native permission-checked
        # repair experiment; their names are local to this helper process.
        return 0x237cd6344, dict(x0=base(s), x1=0x4000)

    steps = [(0x237cd5d8c, dict(x0=0,x1=0x4000,x2=3,x3=0x1002,
                               x4=0xffffffffffffffff,x5=0)),
             lambda f,s:(0x18c3738e0, dict(x0=base(s),x1=0x4000)),
             lambda f,s:(0x237ccfcb4 if control else 0x237ce46ac,
                         dict(x0=self_port(f),x1=source_pid,x2=base(s))),
             query, cleanup]
    bp = target.BreakpointCreateByAddress(frame.FindRegister('pc').GetValueAsUnsigned())
    error = bp.SetScriptCallbackFunction(__name__ + '.callback')
    if error is not None and error.Fail():
        target.BreakpointDelete(bp.GetID())
        raise RuntimeError(str(error))
    calls.begin(frame, steps, 'bootstrap-port-query')


def entry(frame, location, _data):
    debugger, slide, pid, process, control = ARMED
    if names._progname(frame.GetThread().GetProcess()) != process:
        return False
    location.GetBreakpoint().SetEnabled(False)
    start(debugger, slide, pid, process, frame, control)
    return False


def arm(debugger, slide, source_pid, process, boundary, control=False):
    global ARMED
    if calls.STATE is not None:
        raise RuntimeError('a native call is active')
    ARMED = debugger, slide, source_pid, process, control
    names.SLIDE[0] = slide
    bp = debugger.GetSelectedTarget().BreakpointCreateByAddress(boundary)
    bp.SetScriptCallbackFunction(__name__ + '.entry')
