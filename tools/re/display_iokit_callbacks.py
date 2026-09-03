"""LLDB callbacks for backboardd's IOKit connect calls (phase 8).

Phase 7 (docs/re/setup-launch-runtime.md) showed the render-server thread
parking forever in __psynch_mutexwait inside IOMFBServer::set_next_update
while dispatching a client command stream, i.e. another backboardd thread
holds that IOMFBServer mutex and never releases it.  A holder that sleeps
without releasing is in a kernel call, so this module records every
IOConnectCallMethod / ScalarMethod / StructMethod / AsyncMethod entry made by
backboardd (selector in x1, connection in x0, backtrace) and plants a return
watch; the entry without a return at the end of the run is the IOMFB method
the DCP model never completes.  Other processes' calls stop the vCPU but are
neither printed nor counted.

    command script import tools/re/display_iokit_callbacks.py
    script display_iokit_callbacks.install(lldb.debugger, <slide>)
"""
import time
import lldb


CONFIG = {}
RET_CONFIG = {}
HITS = {}
SLIDE = [0]
T0 = [0.0]
STATE = {"srv": None}
CACHE_LO = 0x180000000
CACHE_HI = 0x340000000
PROGNAME_PTR = 0x1e6ef1590
BKD = "backboardd"


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


def _plant_return(target, lr, label, tp, extra):
    bp = target.BreakpointCreateByAddress(lr)
    bp.SetScriptCallbackFunction("display_iokit_callbacks.on_return")
    RET_CONFIG[bp.GetID()] = {"label": label + "_RET", "tp": tp, "t": time.time(), "extra": extra, "hits": 0}


def on_return(frame, bp_loc, _dict):
    bp = bp_loc.GetBreakpoint()
    bp_id = bp.GetID()
    cfg = RET_CONFIG.get(bp_id)
    if cfg is None:
        return False
    tp = _reg(frame, "tpidr_el1")
    cfg["hits"] += 1
    if tp != cfg["tp"] and cfg["hits"] < 16:
        return False
    x0 = _reg(frame, "x0")
    print("=== %s hit=1/1 t=%.3f ===" % (cfg["label"], time.time()))
    print("progname=%s breakpoint=%d thread=0x%x elapsed=%.1fs" %
          (progname(frame.GetThread().GetProcess()), bp_id, frame.GetThread().GetThreadID(), _elapsed()))
    print("registers x0=%s tpidr_el1=0x%x elapsed_in_call=%.3fs %s" %
          ("0x%x" % x0 if x0 is not None else "?", tp or 0, time.time() - cfg["t"], cfg["extra"]))
    frame.GetThread().GetProcess().GetTarget().BreakpointDelete(bp_id)
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
        return False
    tp = _reg(frame, "tpidr_el1")
    if cfg.get("srv_only") and (STATE["srv"] is None or tp != STATE["srv"]):
        return False
    hit = HITS.get(bp_id, 0) + 1
    HITS[bp_id] = hit
    lr = _reg(frame, "lr")
    if cfg["label"] == "SRV_THREAD_ENTRY" and name == BKD and STATE["srv"] is None:
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
                   for i in range(min(thread.GetNumFrames(), 14))]
            print("backtrace " + " ".join(pcs))
        except Exception as e:
            print("backtrace <error %s>" % e)
    if cfg.get("ret") and lr and len(RET_CONFIG) < 48:
        try:
            sel = _reg(frame, "x1")
            _plant_return(process.GetTarget(), lr & 0x0000ffffffffffff, cfg["label"], tp,
                          "selector=0x%x" % (sel if sel is not None else 0))
        except Exception as e:
            print("return-watch <error %s>" % e)
    if hit >= cfg["limit"]:
        bp.SetEnabled(False)
        print("bounded-disabled breakpoint=%d after=%d" % (bp_id, hit))
    return False


def _install(target, interpreter, address, label, regs, limit, bt=False, ret=False, only=None, srv_only=False):
    before = target.GetNumBreakpoints()
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint set --address 0x%x" % address, result)
    print(result.GetOutput() or result.GetError())
    if target.GetNumBreakpoints() != before + 1:
        raise RuntimeError("failed breakpoint %s at 0x%x" % (label, address))
    bp = target.GetBreakpointAtIndex(before)
    bp_id = bp.GetID()
    CONFIG[bp_id] = {"label": label, "regs": regs.split(), "limit": limit, "bt": bt, "ret": ret,
                     "only": only, "srv_only": srv_only}
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand(
        "breakpoint command add -F display_iokit_callbacks.on_break %d" % bp_id, result)
    if not result.Succeeded():
        raise RuntimeError("callback attach failed for %d: %s" % (bp_id, result.GetError()))
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint command list %d" % bp_id, result)
    print("COMMAND_LIST_PROOF id=%d label=%s address=0x%x\n%s" %
          (bp_id, label, address, result.GetOutput() or result.GetError()))


_BASE = "pc lr sp tpidr_el1"

# (static VA, label, regs, limit, backtrace, return-watch, only-process, server-thread-only)
ENTRIES = [
    (0x1843f9664, "SRV_THREAD_ENTRY", "x0", 4, False, False, None, False),
    (0x1843f9804, "SRV_MACHMSG_RET", "w0", 400, False, False, None, False),
    (0x237cd60d4, "SRV_PSYNCH_MUTEXWAIT", "x0 x1 x2", 40, True, False, None, True),
    # IOKit connect calls made by backboardd: entry + return, selector in x1
    (0x18efd97cc, "IOCONNECT_CALL_METHOD", "x0 x1 x2 w3 x4 w5", 300, True, True, BKD, False),
    (0x18efd937c, "IOCONNECT_CALL_SCALAR", "x0 x1 x2 w3", 300, True, True, BKD, False),
    (0x18efd9620, "IOCONNECT_CALL_STRUCT", "x0 x1 x2 x3", 300, True, True, BKD, False),
    (0x18eff3ae0, "IOCONNECT_CALL_ASYNC", "x0 x1 x2 x3", 100, True, True, BKD, False),
    # backboardd condition waits (any thread), to catch a holder parked under the IOMFBServer lock
    (0x18053e2b4, "BKD_PTHREAD_COND_WAIT", "x0 x1", 60, True, False, BKD, False),
    (0x18053d028, "BKD_PTHREAD_COND_TIMEDWAIT", "x0 x1 x2", 60, True, False, BKD, False),
    (0x18053c04c, "BKD_PTHREAD_COND_TIMEDWAIT_REL", "x0 x1 x2", 60, True, False, BKD, False),
    (0x1844e47c8, "QD_ENTRY", "x0", 40, False, False, None, False),
    (0x1847574ec, "CAWS_ENABLE_OOP_OBSERVATION", "x0", 8, False, False, None, False),
    (0x2244d0ba4, "SB_ADFL_ENTRY", "x0 x2", 8, False, False, None, False),
]


def install(debugger, slide):
    SLIDE[0] = slide
    target = debugger.GetSelectedTarget()
    interpreter = debugger.GetCommandInterpreter()
    for static, label, regs, limit, bt, ret, only, srv_only in ENTRIES:
        _install(target, interpreter, static + slide, label, _BASE + " " + regs, limit, bt, ret, only, srv_only)
