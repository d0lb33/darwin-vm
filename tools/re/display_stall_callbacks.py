"""LLDB callbacks for the render-server stall (phase 4).

Phase 3 (docs/re/setup-launch-runtime.md) showed backboardd's
CA::Render::Server thread servicing its MIG port in one burst ~100 s after
creation and never again, leaving SpringBoard blocked in _CASGetDisplays.
This module times the server thread's receive loop and the display swap path:

  * SRV_MACHMSG_RET     server_thread's mach_msg return (0x1843f9804): a
                        heartbeat of the MIG loop, one hit per message
  * RENDER_FOR_TIME     CA::WindowServer::Server::render_for_time entry/return
  * SWAP_*              IOMFBDisplay::swap_wait, CA::IOMobileFramebuffer::
                        swap_wait, kern_SwapBegin/SetLayer/End/Wait entry/return
  * RELBUF_WAIT         IOMFBDisplay::wait_for_relbuf_info entry/return
  * VSYNC_*             IOMFBServer vsync source add/enable and vsync_callback
  * QD_* / SB_ADFL      client display queries and SpringBoard's delegate

"Return" is watched by planting a one-shot breakpoint at the entry hit's lr
(deleted on its first hit in the same process name).  Every hit prints the
process name and host time.

    command script import tools/re/display_stall_callbacks.py
    script display_stall_callbacks.install(lldb.debugger, <slide>)
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
    bp.SetScriptCallbackFunction("display_stall_callbacks.on_return")
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
    print("progname=%s breakpoint=%d thread=0x%x" % (name, bp_id, frame.GetThread().GetThreadID()))
    print("registers x0=%s w0=%s elapsed_in_call=%.3fs" %
          ("0x%x" % x0 if x0 is not None else "?", "0x%x" % (x0 & 0xffffffff) if x0 is not None else "?",
           time.time() - cfg["t"]))
    target = frame.GetThread().GetProcess().GetTarget()
    target.BreakpointDelete(bp_id)
    del RET_CONFIG[bp_id]
    return False


def on_break(frame, bp_loc, _dict):
    bp = bp_loc.GetBreakpoint()
    bp_id = bp.GetID()
    cfg = CONFIG[bp_id]
    thread = frame.GetThread()
    process = thread.GetProcess()
    hit = HITS.get(bp_id, 0) + 1
    HITS[bp_id] = hit
    lr = _reg(frame, "lr")
    name = progname(process)
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
                   for i in range(min(thread.GetNumFrames(), 12))]
            print("backtrace " + " ".join(pcs))
        except Exception as e:
            print("backtrace <error %s>" % e)
    if cfg.get("ret") and lr and len(RET_CONFIG) < 32:
        try:
            _plant_return(process.GetTarget(), lr & 0x0000ffffffffffff, cfg["label"], name)
        except Exception as e:
            print("return-watch <error %s>" % e)
    if hit >= cfg["limit"]:
        bp.SetEnabled(False)
        print("bounded-disabled breakpoint=%d after=%d" % (bp_id, hit))
    return False


def _install(target, interpreter, address, label, regs, limit, bt=False, ret=False):
    before = target.GetNumBreakpoints()
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint set --address 0x%x" % address, result)
    print(result.GetOutput() or result.GetError())
    if target.GetNumBreakpoints() != before + 1:
        raise RuntimeError("failed breakpoint %s at 0x%x" % (label, address))
    bp = target.GetBreakpointAtIndex(before)
    bp_id = bp.GetID()
    CONFIG[bp_id] = {"label": label, "regs": regs.split(), "limit": limit, "bt": bt, "ret": ret}
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand(
        "breakpoint command add -F display_stall_callbacks.on_break %d" % bp_id, result)
    if not result.Succeeded():
        raise RuntimeError("callback attach failed for %d: %s" % (bp_id, result.GetError()))
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint command list %d" % bp_id, result)
    print("COMMAND_LIST_PROOF id=%d label=%s address=0x%x\n%s" %
          (bp_id, label, address, result.GetOutput() or result.GetError()))


_BASE = "pc lr sp tpidr_el1"

# (static VA, label, regs, limit, backtrace, return-watch)
ENTRIES = [
    # CA::Render::Server::server_thread receive loop: mach_msg returns
    (0x1843f9804, "SRV_MACHMSG_RET", "w0", 400, False, False),
    (0x1843f986c, "SRV_MACHMSG2_RET", "w0", 100, False, False),
    (0x1843f9664, "SRV_THREAD_ENTRY", "x0", 4, False, False),
    # server-side display query and render
    (0x18440c6fc, "XGD_SERVER_GET_DISPLAYS", "x0 x1 x2", 32, False, False),
    (0x18440fc74, "RENDER_FOR_TIME", "x0 x1 x2 x3", 60, False, True),
    # swap path
    (0x18445bd00, "SWAP_TRY_BEGIN_ASYNC", "x0 w1", 32, False, True),
    (0x22a3911cc, "KERN_SWAP_BEGIN", "x0 x1 x2", 32, True, True),
    (0x22a3918f4, "KERN_SWAP_SET_LAYER", "x0 x1 x2 x3", 32, False, True),
    (0x22a391334, "KERN_SWAP_END", "x0 x1", 32, False, True),
    (0x22a3912b4, "KERN_SWAP_WAIT", "x0 w1 w2", 32, True, True),
    (0x18440fab0, "CA_IOMFB_SWAP_WAIT", "x0 w1 w2", 32, False, True),
    (0x18443027c, "IOMFBDISPLAY_SWAP_WAIT", "x0 w1 w2 w3", 32, True, True),
    (0x1843eaed4, "RELBUF_WAIT", "x0 x1", 16, True, True),
    # vsync plumbing
    (0x184476330, "VSYNC_ADD_SOURCE", "x0", 8, True, True),
    (0x18455c7d0, "VSYNC_SET_ENABLED", "x0", 8, False, True),
    (0x1843eb030, "VSYNC_CALLBACK", "x0 x1 x2 x3", 40, False, False),
    (0x1843ea5b4, "IOMFB_TIMER_CALLBACK", "x0 x1", 40, False, False),
    # clients and SpringBoard
    (0x1844e47c8, "QD_ENTRY", "x0", 40, False, False),
    (0x1844e484c, "QD_CASGETDISPLAYS_RET", "w0", 40, False, False),
    (0x1847574ec, "CAWS_ENABLE_OOP_OBSERVATION", "x0", 8, False, False),
    (0x18490d11c, "UIAPPLICATION_MAIN", "x0", 16, False, False),
    (0x2244d0ba4, "SB_ADFL_ENTRY", "x0 x2", 8, False, False),
    (0x187f727a4, "LIBC_ABORT", "x0", 16, True, False),
]


def install(debugger, slide):
    SLIDE[0] = slide
    target = debugger.GetSelectedTarget()
    interpreter = debugger.GetCommandInterpreter()
    for static, label, regs, limit, bt, ret in ENTRIES:
        _install(target, interpreter, static + slide, label, _BASE + " " + regs, limit, bt, ret)
