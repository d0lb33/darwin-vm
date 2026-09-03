"""LLDB callbacks for the display open/attach timeline (phase 6).

Phase 5 (docs/re/setup-launch-runtime.md) showed backboardd's render server
thread starting at 15.8 s, the AppleDisplay constructor reaching
end_display_changes at 84.6 s, and the display state transitions plus the
first servicing of client requests at 115.8 s.  This module times that chain
(entry + return via a one-shot breakpoint at the caller's lr) and counts
port-set membership changes only when the caller is backboardd, so other
processes cannot exhaust the limits.

    command script import tools/re/display_open_callbacks.py
    script display_open_callbacks.install(lldb.debugger, <slide>)
"""
import time
import lldb


CONFIG = {}
RET_CONFIG = {}
HITS = {}
SLIDE = [0]
T0 = [0.0]
CACHE_LO = 0x180000000
CACHE_HI = 0x340000000
PROGNAME_PTR = 0x1e6ef1590
DISPLAY_STATES = ["off"]  # only index 0 read from display_state_to_string (0x1845c2344); others print raw


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


def _plant_return(target, lr, label, name):
    bp = target.BreakpointCreateByAddress(lr)
    bp.SetScriptCallbackFunction("display_open_callbacks.on_return")
    RET_CONFIG[bp.GetID()] = {"label": label + "_RET", "progname": name, "hits": 0, "t": time.time()}


def on_return(frame, bp_loc, _dict):
    bp = bp_loc.GetBreakpoint()
    bp_id = bp.GetID()
    cfg = RET_CONFIG.get(bp_id)
    if cfg is None:
        return False
    process = frame.GetThread().GetProcess()
    name = progname(process)
    cfg["hits"] += 1
    if name != cfg["progname"] and cfg["hits"] < 8:
        return False
    x0 = _reg(frame, "x0")
    print("=== %s hit=1/1 t=%.3f ===" % (cfg["label"], time.time()))
    print("progname=%s breakpoint=%d thread=0x%x elapsed=%.1fs" %
          (name, bp_id, frame.GetThread().GetThreadID(), _elapsed()))
    print("registers x0=%s tpidr_el1=0x%x elapsed_in_call=%.3fs" %
          ("0x%x" % x0 if x0 is not None else "?", _reg(frame, "tpidr_el1") or 0, time.time() - cfg["t"]))
    process.GetTarget().BreakpointDelete(bp_id)
    del RET_CONFIG[bp_id]
    return False


def on_break(frame, bp_loc, _dict):
    bp = bp_loc.GetBreakpoint()
    bp_id = bp.GetID()
    cfg = CONFIG[bp_id]
    thread = frame.GetThread()
    process = thread.GetProcess()
    name = progname(process)
    if cfg.get("only") and name != cfg["only"]:
        return False                       # not counted, not printed
    hit = HITS.get(bp_id, 0) + 1
    HITS[bp_id] = hit
    lr = _reg(frame, "lr")
    print("=== %s hit=%d/%d t=%.3f ===" % (cfg["label"], hit, cfg["limit"], time.time()))
    print("progname=%s breakpoint=%d thread=0x%x elapsed=%.1fs" %
          (name, bp_id, thread.GetThreadID(), _elapsed()))
    values = []
    for rn in cfg["regs"]:
        value = _reg(frame, rn)
        values.append("%s=%s" % (rn, "<invalid>" if value is None else "0x%x" % value))
    print("registers " + " ".join(values))
    print("lr_static=%s" % (_fmt_addr(lr) if lr is not None else "?"))
    if cfg.get("state"):
        st = _reg(frame, "x1")
        print("display_state=%s" % (DISPLAY_STATES[st] if st is not None and st < len(DISPLAY_STATES) else st))
    if cfg.get("bt"):
        try:
            pcs = [_fmt_addr(thread.GetFrameAtIndex(i).GetPC() & 0x0000ffffffffffff)
                   for i in range(min(thread.GetNumFrames(), 12))]
            print("backtrace " + " ".join(pcs))
        except Exception as e:
            print("backtrace <error %s>" % e)
    if cfg.get("ret") and lr and len(RET_CONFIG) < 40:
        try:
            _plant_return(process.GetTarget(), lr & 0x0000ffffffffffff, cfg["label"], name)
        except Exception as e:
            print("return-watch <error %s>" % e)
    if hit >= cfg["limit"]:
        bp.SetEnabled(False)
        print("bounded-disabled breakpoint=%d after=%d" % (bp_id, hit))
    return False


def _install(target, interpreter, address, label, regs, limit, bt=False, ret=False, only=None, state=False):
    before = target.GetNumBreakpoints()
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint set --address 0x%x" % address, result)
    print(result.GetOutput() or result.GetError())
    if target.GetNumBreakpoints() != before + 1:
        raise RuntimeError("failed breakpoint %s at 0x%x" % (label, address))
    bp = target.GetBreakpointAtIndex(before)
    bp_id = bp.GetID()
    CONFIG[bp_id] = {"label": label, "regs": regs.split(), "limit": limit, "bt": bt, "ret": ret,
                     "only": only, "state": state}
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand(
        "breakpoint command add -F display_open_callbacks.on_break %d" % bp_id, result)
    if not result.Succeeded():
        raise RuntimeError("callback attach failed for %d: %s" % (bp_id, result.GetError()))
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint command list %d" % bp_id, result)
    print("COMMAND_LIST_PROOF id=%d label=%s address=0x%x\n%s" %
          (bp_id, label, address, result.GetOutput() or result.GetError()))


_BASE = "pc lr sp tpidr_el1"
BKD = "backboardd"

# (static VA, label, regs, limit, backtrace, return-watch, only-process, state-decode)
ENTRIES = [
    # the open/attach chain, entry + return
    (0x184756b7c, "CAWS_SERVER_WITH_OPTIONS", "x0 x2", 4, False, True, None, False),
    (0x184756bdc, "CAWS_SHARED_SERVER_INIT", "x0", 4, False, True, None, False),
    (0x18475767c, "CAWS_DETECT_DISPLAYS", "x0", 8, False, True, None, False),
    (0x1845f7cb0, "AID_OPEN", "x0 x1", 8, False, True, None, False),
    (0x1845f36b4, "APPLEDISPLAY_CTOR", "x0 x1 x2 x3 x4", 8, False, True, None, False),
    (0x1847ae4c4, "IOMFBDISPLAY_UPDATE_FB_LOCKED", "x0 w1", 16, False, True, None, False),
    (0x1846826ac, "DISPLAY_END_DISPLAY_CHANGES", "x0", 16, False, True, None, False),
    (0x1843f3ba4, "IOMFBDISPLAY_INIT_TIMINGS", "x0", 8, False, True, None, False),
    (0x22a392954, "IOMFB_OPEN", "x0 x1 x2", 8, True, True, None, False),
    (0x22a395f2c, "IOMFB_GET_MAIN_DISPLAY", "x0", 8, False, True, None, False),
    (0x1843f9664, "SRV_THREAD_ENTRY", "x0", 4, False, False, None, False),
    (0x1845bebf0, "SRV_SERVER_PORT", "x0", 4, True, True, None, False),
    # port-set membership, backboardd only
    (0x237cd59f8, "MACH_PORT_INSERT_MEMBER", "w0 w1 w2", 60, True, False, BKD, False),
    (0x237cd8c1c, "MACH_PORT_MOVE_MEMBER", "w0 w1 w2", 60, True, False, BKD, False),
    (0x237cd5a6c, "MACH_PORT_EXTRACT_MEMBER", "w0 w1 w2", 60, True, False, BKD, False),
    (0x1805c1424, "BOOTSTRAP_CHECK_IN", "x0 x1 x2", 20, True, True, BKD, False),
    # display state machine (x1 decoded), power
    (0x1845bf80c, "WS_SET_DISPLAY_STATE", "x0 x1 x2 x3", 40, False, False, None, True),
    (0x184476118, "IOMFB_COMPLETE_STATE_TRANSITION", "x0 x1 x2", 40, False, False, None, True),
    (0x1845c0764, "IOMFBDISPLAY_SET_POWER_STATE", "x0 x1 x2", 40, False, False, None, False),
    (0x1845767f8, "IOMFB_CHECK_DISPLAY_BLANKED", "x0 x1", 24, False, True, None, False),
    # server loop heartbeat with message header, clients, SpringBoard
    (0x1843f9804, "SRV_MACHMSG_RET", "w0", 400, False, False, None, False),
    (0x1844e47c8, "QD_ENTRY", "x0", 40, False, False, None, False),
    (0x1844e484c, "QD_CASGETDISPLAYS_RET", "w0", 40, False, False, None, False),
    (0x1847574ec, "CAWS_ENABLE_OOP_OBSERVATION", "x0", 8, False, False, None, False),
    (0x18490d11c, "UIAPPLICATION_MAIN", "x0", 16, False, False, None, False),
    (0x2244d0ba4, "SB_ADFL_ENTRY", "x0 x2", 8, False, False, None, False),
    (0x187f727a4, "LIBC_ABORT", "x0", 16, True, False, None, False),
]


def install(debugger, slide):
    SLIDE[0] = slide
    target = debugger.GetSelectedTarget()
    interpreter = debugger.GetCommandInterpreter()
    for static, label, regs, limit, bt, ret, only, state in ENTRIES:
        _install(target, interpreter, static + slide, label, _BASE + " " + regs, limit, bt, ret, only, state)
