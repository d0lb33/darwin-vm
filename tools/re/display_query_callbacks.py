"""LLDB callbacks for the CoreAnimation display-query refusal (phase 3).

Phase 2 (docs/re/setup-launch-runtime.md) found UIKit daemons aborting in
QuartzCore's query_displays() with "CoreAnimation: Unable to query displays
from server (%d)", and statically that backboardd creates its CAWindowServer
with kCAWindowServerDisableOutOfProcessDisplayObservation, so the render
server's __XGetDisplays answers every other process with 0xfb294002 until
-[CAWindowServer enableOutOfProcessDisplayObservation] clears
CA::Render::Server::_oop_display_observation_disabled.  This module records,
per process (libsystem_c ___progname_pointer) and with a frame-pointer
backtrace in static cache addresses:

  * client side: query_displays entry, the server-port check, the
    _CASGetDisplays return code, the retry-timeout/abort edge, abort()
  * server side: __XGetDisplays' pid check and refusal, and the three enable
    entry points (ObjC method, C API, MIG request)
  * UIKit/SpringBoard: _UIApplicationMain, -[SpringBoard
    applicationDidFinishLaunching:], _finalizeStartupAfterScenesDidConnect:
  * backboardd: on its first query_displays hit the PIE base is found by
    scanning down from a main-executable frame for the Mach-O header, and the
    StartWindowServer gates from docs/re/backboardd-start-window-server-gate.md
    are installed at B+0x259fc/B+0x25a00 (x21/x22 null tests), B+0x25fdc
    (headless log), B+0x25f74 (setup complete).

    command script import tools/re/display_query_callbacks.py
    script display_query_callbacks.install(lldb.debugger, <slide>)
"""
import time
import lldb


CONFIG = {}
HITS = {}
SLIDE = [0]
STATE = {"bkd_base": None, "bkd_installed": False}
CACHE_LO = 0x180000000
CACHE_HI = 0x340000000
PROGNAME_PTR = 0x1e6ef1590      # libsystem_c ___progname_pointer

# backboardd static offsets (PIE __TEXT at 0x100000000), from
# docs/re/backboardd-start-window-server-gate.md; installed at runtime as B+off.
BKD_GATES = [
    (0x259fc, "BKD_WS_MAINDISPLAY_NULLTEST", "x21", []),
    (0x25a00, "BKD_WS_SERVERDISPLAY_NULLTEST", "x21 x22", []),
    (0x25fdc, "BKD_WS_HEADLESS_LOG", "x0 x2 x3", []),
    (0x25ef0, "BKD_WS_SERVER_IF_RUNNING_CALL", "x0", []),
    (0x25f48, "BKD_WS_ENABLE_OOP_CALL", "x0 x19", []),
    (0x25f74, "BKD_WS_SETUP_COMPLETE", "x0", []),
]


def _reg(frame, name):
    value = frame.FindRegister(name)
    if not value.IsValid():
        return None
    return value.GetValueAsUnsigned()


def _read(process, address, size):
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    if not error.Success():
        return None
    return data


def _hex(data):
    return "<read-error>" if data is None else data.hex()


def _u64(process, address):
    data = _read(process, address, 8)
    return None if data is None else int.from_bytes(data, "little")


def _u32(process, address):
    data = _read(process, address, 4)
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
    return "0x%x" % st if st is not None else "0x%x!" % addr   # '!' = outside the cache


def backtrace(thread, limit=14):
    """Frame-pointer unwind via LLDB; PCs printed as static cache VAs, '!' marks non-cache."""
    out = []
    for i in range(min(thread.GetNumFrames(), limit)):
        f = thread.GetFrameAtIndex(i)
        out.append(_fmt_addr(f.GetPC() & 0x0000ffffffffffff))
    return " ".join(out)


def _find_macho_base(process, addr, span=0x8000000):
    """Scan down from addr, page by page, for a 64-bit arm64 Mach-O header."""
    page = addr & ~0x3fff
    lo = max(page - span, 0x100000000)
    while page >= lo:
        w = _u32(process, page)
        # MH_MAGIC_64, CPU_TYPE_ARM64, and filetype MH_EXECUTE (2): non-cache
        # dylibs are mapped above the executable and also match the magic
        # (UI_DISPLAY_QUERY1 found one at 0x102460000, 32 MB above backboardd).
        if (w == 0xfeedfacf and _u32(process, page + 4) == 0x0100000c
                and _u32(process, page + 12) == 2):
            return page
        page -= 0x4000
    return None


def _install_bkd_gates(target, interpreter, base):
    for off, label, regs, reads in BKD_GATES:
        _install(target, interpreter, base + off, label, _BASE + " " + regs, reads, 8)
    STATE["bkd_installed"] = True
    print("BKD_GATES installed at base 0x%x" % base)


def _maybe_resolve_backboardd(frame, thread, process):
    if STATE["bkd_installed"]:
        return
    # any frame PC outside the cache in the PIE range is inside backboardd's own text
    for i in range(min(thread.GetNumFrames(), 20)):
        pc = thread.GetFrameAtIndex(i).GetPC() & 0x0000ffffffffffff
        if _static(pc) is None and 0x100000000 <= pc < 0x140000000:
            base = _find_macho_base(process, pc)
            if base is not None:
                STATE["bkd_base"] = base
                print("BKD_BASE resolved from frame %d pc=0x%x -> base=0x%x" % (i, pc, base))
                debugger = lldb.debugger
                _install_bkd_gates(debugger.GetSelectedTarget(), debugger.GetCommandInterpreter(), base)
                return
    print("BKD_BASE not resolved on this hit")


def on_break(frame, bp_loc, _dict):
    bp = bp_loc.GetBreakpoint()
    bp_id = bp.GetID()
    cfg = CONFIG[bp_id]
    thread = frame.GetThread()
    process = thread.GetProcess()
    hit = HITS.get(bp_id, 0) + 1
    HITS[bp_id] = hit
    pc = _reg(frame, "pc") or 0
    lr = _reg(frame, "lr")
    name = progname(process)
    print("=== %s hit=%d/%d t=%.3f ===" % (cfg["label"], hit, cfg["limit"], time.time()))
    print("progname=%s breakpoint=%d thread=0x%x" % (name, bp_id, thread.GetThreadID()))
    values = []
    for rn in cfg["regs"]:
        value = _reg(frame, rn)
        values.append("%s=%s" % (rn, "<invalid>" if value is None else "0x%x" % value))
    print("registers " + " ".join(values))
    print("lr_static=%s" % _fmt_addr(lr) if lr is not None else "lr_static=?")
    for label, base, offset, size in cfg["reads"]:
        if isinstance(base, int):
            address = base + offset
        else:
            base_value = _reg(frame, base)
            if base_value is None:
                print("field %s=<invalid-base:%s>" % (label, base))
                continue
            address = base_value + offset
        print("field %s@0x%x=%s" % (label, address, _hex(_read(process, address, size))))
    if cfg.get("bt"):
        try:
            print("backtrace " + backtrace(thread))
        except Exception as e:  # never let diagnostics stop the run
            print("backtrace <error %s>" % e)
    if cfg.get("bkd") and name == "backboardd":
        try:
            _maybe_resolve_backboardd(frame, thread, process)
        except Exception as e:
            print("BKD_BASE <error %s>" % e)
    if hit >= cfg["limit"]:
        bp.SetEnabled(False)
        print("bounded-disabled breakpoint=%d after=%d" % (bp_id, hit))
    return False


def _install(target, interpreter, address, label, regs, reads, limit, bt=False, bkd=False):
    before = target.GetNumBreakpoints()
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint set --address 0x%x" % address, result)
    print(result.GetOutput() or result.GetError())
    if target.GetNumBreakpoints() != before + 1:
        raise RuntimeError("failed breakpoint %s at 0x%x" % (label, address))
    bp = target.GetBreakpointAtIndex(before)
    bp_id = bp.GetID()
    CONFIG[bp_id] = {"label": label, "regs": regs.split(), "reads": reads,
                     "limit": limit, "bt": bt, "bkd": bkd}
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand(
        "breakpoint command add -F display_query_callbacks.on_break %d" % bp_id, result)
    if not result.Succeeded():
        raise RuntimeError("callback attach failed for %d: %s" % (bp_id, result.GetError()))
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint command list %d" % bp_id, result)
    print("COMMAND_LIST_PROOF id=%d label=%s address=0x%x\n%s" %
          (bp_id, label, address, result.GetOutput() or result.GetError()))


_BASE = "pc lr sp tpidr_el1"
OOP_FLAG = 0x1e40102a6   # CA::Render::Server::_oop_display_observation_disabled (QuartzCore __bss)

# (static VA, label, extra regs, reads, limit, backtrace?, backboardd-resolve?)
ENTRIES = [
    # client side, QuartzCore query_displays()
    (0x1844e47c8, "QD_ENTRY", "x0", [("oop_disabled_flag", "abs", OOP_FLAG, 1)], 40, True, True),
    (0x1844e4838, "QD_SERVER_PORT", "w0", [], 40, False, False),
    (0x1844e484c, "QD_CASGETDISPLAYS_RET", "w0", [("count_out", "fp", -0x64, 4)], 40, False, False),
    (0x1844e4e54, "QD_ERROR_EDGE", "w19", [], 16, True, False),
    (0x1844e4d28, "QD_NOSERVER_RETURN", "x0", [], 16, True, False),
    (0x187f727a4, "LIBC_ABORT", "x0", [], 16, True, False),
    # server side, QuartzCore __XGetDisplays: after the flag load (w8) with client pid (w21), flag arg (w20)
    (0x18440c568, "XGD_PID_CHECK", "w8 w20 w21", [], 40, False, False),
    (0x18440c574, "XGD_PID_COMPARE", "w0 w21", [], 40, False, False),
    (0x18440c6d0, "XGD_REFUSED_FB294002", "x19", [], 16, False, False),
    (0x18440c6fc, "XGD_SERVER_GET_DISPLAYS", "x0 x1 x2", [], 16, False, False),
    # the three ways _oop_display_observation_disabled gets cleared
    (0x1847574ec, "CAWS_ENABLE_OOP_OBSERVATION", "x0", [], 8, True, False),
    (0x1846d7828, "CARS_ENABLE_OOP_OBSERVATION_API", "x0", [], 8, True, False),
    (0x1847da7dc, "XENABLE_OOP_OBSERVATION_MIG", "x0 x1", [], 8, True, False),
    # UIKit / SpringBoard startup
    (0x18490d11c, "UIAPPLICATION_MAIN", "x0 x1 x2 x3", [], 16, False, False),
    (0x2244d0ba4, "SB_ADFL_ENTRY", "x0 x2", [], 8, False, False),
    (0x2244d8cf4, "SB_FINALIZE_ENTRY", "x0 x2", [], 8, False, False),
    (0x22433ff18, "SB_ACTIVATE_ENTRY", "x0 x1", [], 8, True, False),
]


def install(debugger, slide):
    SLIDE[0] = slide
    target = debugger.GetSelectedTarget()
    interpreter = debugger.GetCommandInterpreter()
    for static, label, regs, reads, limit, bt, bkd in ENTRIES:
        fixed = []
        for rlabel, base, offset, size in reads:
            if base == "abs":
                fixed.append((rlabel, slide + offset, 0, size))
            else:
                fixed.append((rlabel, base, offset, size))
        _install(target, interpreter, static + slide, label, _BASE + " " + regs, fixed, limit, bt, bkd)
