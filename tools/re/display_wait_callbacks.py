"""LLDB callbacks for the render-server thread's condition wait (phase 7).

Phase 6 (docs/re/setup-launch-runtime.md) found backboardd's
CA::Render::Server thread parked in a pthread condition wait on a heap object
after its first burst of client servicing.  This module records the server
thread's identity at server_thread entry (tpidr_el1) and then reports only
that thread's pthread_cond_wait / pthread_cond_timedwait / __psynch_mutexwait
entries with x0 (cond) x1 (mutex) and a frame-pointer backtrace, so the
QuartzCore call site of the wait is named.  Everything else is unchanged from
phase 4 (heartbeat, client queries, SpringBoard markers).

    command script import tools/re/display_wait_callbacks.py
    script display_wait_callbacks.install(lldb.debugger, <slide>)
"""
import time
import lldb


CONFIG = {}
HITS = {}
SLIDE = [0]
T0 = [0.0]
STATE = {"srv": None, "cond_seen": {}}
CACHE_LO = 0x180000000
CACHE_HI = 0x340000000
PROGNAME_PTR = 0x1e6ef1590


def _reg(frame, name):
    value = frame.FindRegister(name)
    if not value.IsValid():
        return None
    return value.GetValueAsUnsigned()


def _read(process, address, size):
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    return data if error.Success() else None


def _u64(process, address):
    data = _read(process, address, 8)
    return None if data is None else int.from_bytes(data, "little")


def _cstring(process, address, limit=128):
    if not address:
        return "<null>"
    data = _read(process, address, limit)
    if data is None:
        return "<read-error>"
    return data.split(b"\0", 1)[0].decode("ascii", "replace")


def progname(process):
    p = _u64(process, SLIDE[0] + PROGNAME_PTR)
    if not p:
        return "<progname-ptr-unreadable>"
    s = _u64(process, p)
    if not s:
        return "<progname-null>"
    return _cstring(process, s)


def _static(addr):
    s = SLIDE[0]
    if addr is not None and CACHE_LO + s <= addr < CACHE_HI + s:
        return addr - s
    return None


def _fmt_addr(addr):
    st = _static(addr)
    return "0x%x" % st if st is not None else "0x%x!" % addr


def _elapsed():
    now = time.time()
    if not T0[0]:
        T0[0] = now
    return now - T0[0]


def on_break(frame, bp_loc, _dict):
    bp = bp_loc.GetBreakpoint()
    bp_id = bp.GetID()
    cfg = CONFIG[bp_id]
    thread = frame.GetThread()
    process = thread.GetProcess()
    tp = _reg(frame, "tpidr_el1")
    if cfg.get("srv_only"):
        if STATE["srv"] is None or tp != STATE["srv"]:
            return False                       # other threads/processes: silent, uncounted
    hit = HITS.get(bp_id, 0) + 1
    HITS[bp_id] = hit
    lr = _reg(frame, "lr")
    name = progname(process)
    if cfg["label"] == "SRV_THREAD_ENTRY" and name == "backboardd" and STATE["srv"] is None:
        STATE["srv"] = tp
        print("SRV_THREAD tpidr_el1=0x%x recorded" % tp)
    print("=== %s hit=%d/%d t=%.3f ===" % (cfg["label"], hit, cfg["limit"], time.time()))
    print("progname=%s breakpoint=%d thread=0x%x elapsed=%.1fs" %
          (name, bp_id, thread.GetThreadID(), _elapsed()))
    values = []
    for rn in cfg["regs"]:
        value = _reg(frame, rn)
        values.append("%s=%s" % (rn, "<invalid>" if value is None else "0x%x" % value))
    print("registers " + " ".join(values))
    print("lr_static=%s" % (_fmt_addr(lr) if lr is not None else "?"))
    if cfg.get("bt"):
        try:
            pcs = [_fmt_addr(thread.GetFrameAtIndex(i).GetPC() & 0x0000ffffffffffff)
                   for i in range(min(thread.GetNumFrames(), 16))]
            print("backtrace " + " ".join(pcs))
        except Exception as e:
            print("backtrace <error %s>" % e)
    if cfg.get("cond"):
        c = _reg(frame, "x0") or 0
        print("cond_object@0x%x=%s mutex@0x%x=%s" % (c, (_read(process, c, 48) or b"").hex(),
              _reg(frame, "x1") or 0, (_read(process, _reg(frame, "x1") or 0, 32) or b"").hex()))
    if hit >= cfg["limit"]:
        bp.SetEnabled(False)
        print("bounded-disabled breakpoint=%d after=%d" % (bp_id, hit))
    return False


def _install(target, interpreter, address, label, regs, limit, bt=False, srv_only=False, cond=False):
    before = target.GetNumBreakpoints()
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint set --address 0x%x" % address, result)
    print(result.GetOutput() or result.GetError())
    if target.GetNumBreakpoints() != before + 1:
        raise RuntimeError("failed breakpoint %s at 0x%x" % (label, address))
    bp = target.GetBreakpointAtIndex(before)
    bp_id = bp.GetID()
    CONFIG[bp_id] = {"label": label, "regs": regs.split(), "limit": limit, "bt": bt,
                     "srv_only": srv_only, "cond": cond}
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand(
        "breakpoint command add -F display_wait_callbacks.on_break %d" % bp_id, result)
    if not result.Succeeded():
        raise RuntimeError("callback attach failed for %d: %s" % (bp_id, result.GetError()))
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint command list %d" % bp_id, result)
    print("COMMAND_LIST_PROOF id=%d label=%s address=0x%x\n%s" %
          (bp_id, label, address, result.GetOutput() or result.GetError()))


_BASE = "pc lr sp tpidr_el1"

# (static VA, label, regs, limit, backtrace, server-thread-only, cond decode)
ENTRIES = [
    (0x1843f9664, "SRV_THREAD_ENTRY", "x0", 4, False, False, False),
    (0x1843f9804, "SRV_MACHMSG_RET", "w0", 400, False, False, False),
    (0x18053e2b4, "SRV_PTHREAD_COND_WAIT", "x0 x1", 40, True, True, True),
    (0x18053d028, "SRV_PTHREAD_COND_TIMEDWAIT", "x0 x1 x2", 40, True, True, True),
    (0x237cd60d4, "SRV_PSYNCH_MUTEXWAIT", "x0 x1 x2", 40, True, True, False),
    (0x1844e47c8, "QD_ENTRY", "x0", 40, False, False, False),
    (0x1844e484c, "QD_CASGETDISPLAYS_RET", "w0", 40, False, False, False),
    (0x1847574ec, "CAWS_ENABLE_OOP_OBSERVATION", "x0", 8, False, False, False),
    (0x18490d11c, "UIAPPLICATION_MAIN", "x0", 16, False, False, False),
    (0x2244d0ba4, "SB_ADFL_ENTRY", "x0 x2", 8, False, False, False),
]


def install(debugger, slide):
    SLIDE[0] = slide
    target = debugger.GetSelectedTarget()
    interpreter = debugger.GetCommandInterpreter()
    for static, label, regs, limit, bt, srv_only, cond in ENTRIES:
        _install(target, interpreter, static + slide, label, _BASE + " " + regs, limit, bt, srv_only, cond)
