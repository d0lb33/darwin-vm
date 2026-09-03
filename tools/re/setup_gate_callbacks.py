"""LLDB breakpoint callbacks for the SetupAssistant launch-gate probe.

Usage (inside lldb attached to QEMU's gdbstub, guest stopped):

    command script import tools/re/setup_gate_callbacks.py
    script setup_gate_callbacks.install(lldb.debugger, <shared-cache slide>)
    continue

Every breakpoint is installed with `breakpoint command add -F` and proven with
`breakpoint command list`; see docs/re/lldb-breakpoint-command-trap.md for why
the `-o a -o b -o continue` form must not be used (LLDB 21 keeps only the last
option).  Callbacks are bounded multi-hit: shared-cache breakpoints fire in
every process that executes the address, so nothing is disabled on first hit.

Static VAs are from the iOS 27 beta 8 cache (UUID 58C54E82-C171-300E-AEEE-
06DF937AA565); the derivation of each site is in docs/re/setup-launch-gate.md.
"""
import lldb


CONFIG = {}
HITS = {}
SLIDE = [0]
CACHE_LO = 0x180000000
CACHE_HI = 0x340000000


def _reg(frame, name):
    value = frame.FindRegister(name)
    if not value.IsValid():
        return None
    return value.GetValueAsUnsigned()


def _read(process, address, size):
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    if not error.Success():
        return "<read-error:%s>" % error.GetCString()
    return data.hex()


def _static(addr):
    """Runtime cache address -> static cache VA, or None if outside the cache."""
    s = SLIDE[0]
    if addr is not None and CACHE_LO + s <= addr < CACHE_HI + s:
        return addr - s
    return None


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
    print("=== %s hit=%d/%d ===" % (cfg["label"], hit, cfg["limit"]))
    print("breakpoint=%d location=%d debugger_pid=%d thread=0x%x" %
          (bp_id, bp_loc.GetID(), process.GetProcessID(), thread.GetThreadID()))
    values = []
    for name in cfg["regs"]:
        value = _reg(frame, name)
        values.append("%s=%s" % (name, "<invalid>" if value is None else "0x%x" % value))
    print("registers " + " ".join(values))
    st = _static(lr)
    print("lr_static=%s" % ("0x%x" % st if st is not None else "<outside-cache>"))
    print("code@0x%x=%s" % (pc, _read(process, pc, 16)))
    for label, base, offset, size in cfg["reads"]:
        if isinstance(base, int):
            address = base + offset
        else:
            base_value = _reg(frame, base)
            if base_value is None:
                print("field %s=<invalid-base:%s>" % (label, base))
                continue
            address = base_value + offset
        print("field %s@0x%x=%s" % (label, address, _read(process, address, size)))
    if cfg.get("objc"):
        obj = _reg(frame, cfg["objc"])
        print("objc_object %s=0x%x bytes=%s" %
              (cfg["objc"], obj or 0, _read(process, obj or 0, 64) if obj else "<null>"))
    if hit >= cfg["limit"]:
        bp.SetEnabled(False)
        print("bounded-disabled breakpoint=%d after=%d" % (bp_id, hit))
    return False


def _install(target, interpreter, address, label, regs, reads, limit, objc=None):
    before = target.GetNumBreakpoints()
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint set --address 0x%x" % address, result)
    print(result.GetOutput() or result.GetError())
    if target.GetNumBreakpoints() != before + 1:
        raise RuntimeError("failed breakpoint %s at 0x%x" % (label, address))
    bp = target.GetBreakpointAtIndex(before)
    bp_id = bp.GetID()
    CONFIG[bp_id] = {"label": label, "regs": regs.split(), "reads": reads,
                     "limit": limit, "objc": objc}
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand(
        "breakpoint command add -F setup_gate_callbacks.on_break %d" % bp_id,
        result)
    if not result.Succeeded():
        raise RuntimeError("callback attach failed for %d: %s" %
                           (bp_id, result.GetError()))
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint command list %d" % bp_id, result)
    print("COMMAND_LIST_PROOF id=%d label=%s address=0x%x\n%s" %
          (bp_id, label, address, result.GetOutput() or result.GetError()))


# Common register set: pc/lr for attribution, sp/tpidr_el1 as a thread identity
# proxy across hits (the gdbstub has no process notion), cpsr for flags.
_BASE = "pc lr sp cpsr tpidr_el0 tpidr_el1"


def install(debugger, slide):
    SLIDE[0] = slide
    target = debugger.GetSelectedTarget()
    interpreter = debugger.GetCommandInterpreter()
    cached = slide + 0x1e72ba900          # __isSupportedDeviceClass.isSupported
    once = slide + 0x1e72ba8f8            # its dispatch_once token
    entries = [
        # _BYSetupAssistantNeedsToRun (SetupAssistant __TEXT 0x1cac10000)
        (0x1cac11c18, "BY_NEEDS_ENTRY", _BASE + " x0 x19 x20",
         [("cached_supported", cached, 0, 1), ("once_token", once, 0, 8)], 16, None),
        (0x1cac11c44, "BY_NON_UI_PREDICATE_RESULT", _BASE + " w0 x0", [], 16, None),
        (0x1cac11ca0, "BY_FORCE_NO_BUDDY_BRANCH", _BASE + " w20 x20", [], 16, None),
        (0x1cac11d20, "BY_DEVICE_CLASS_CACHED_LOAD", _BASE + " w8 x8",
         [("cached_supported", cached, 0, 1)], 16, None),
        (0x1cac11d28, "BY_DEVICE_CLASS_CACHED_BRANCH", _BASE + " w8 x8",
         [("cached_supported", cached, 0, 1)], 16, None),
        (0x1cac11df4, "BY_UNSUPPORTED_DEVICE_FALSE_EDGE", _BASE + " w8 x8",
         [("cached_supported", cached, 0, 1)], 16, None),
        # after `bl _LaunchSentinelExists`: x0 = sem_open("purplebuddy.sentinel") != -1
        (0x1cac11d30, "BY_LAUNCH_SENTINEL_RESULT", _BASE + " w0 x0 x20 x21", [], 16, None),
        # after the internal-content check: w0 == 0 returns w20 (= !sentinel) directly
        (0x1cac11d44, "BY_INTERNAL_CONTENT_BRANCH", _BASE + " w0 x0 w20 x20 x21", [], 16, None),
        (0x1cac11cec, "BY_NEEDS_COMMON_RETURN_PREMOVE", _BASE + " x0 w20 x20 x21",
         [("cached_supported", cached, 0, 1)], 32, None),
        (0x1cac11cf0, "BY_NEEDS_COMMON_RETURN_RESULT", _BASE + " x0 w0 x20",
         [("cached_supported", cached, 0, 1)], 32, None),
        # ___isSupportedDeviceClass_block_invoke, right after _MGGetSInt32Answer
        (0x1cac11fc8, "BY_DEVICE_CLASS_QUERY_RETURN", _BASE + " w0 x0",
         [("cached_supported_before_store", cached, 0, 1)], 16, None),
        (0x1cac23394, "BY_PREPARE_LAUNCH_SENTINEL", _BASE + " x0 x1 x2 x3", [], 16, None),
        # SpringBoard: -[SBLockScreenManager _maybeLaunchSetupForcingCheckIfNotBricked:]_block_invoke+128
        (0x224c4fb78, "SB_WORKSPACE_ACTIVATE_SETUP_CALL", _BASE + " x0 x1 x2 x3 x19 x20 x21", [], 16, "x0"),
        (0x224c4fb7c, "SB_WORKSPACE_ACTIVATE_SETUP_RETURN", _BASE + " w0 x0 x19 x20 x21", [], 16, None),
        # libMobileGestalt _MobileGestalt_get_deviceClassNumber: w21 = answer or -1
        (0x1af732264, "MG_DEVICE_CLASS_HELPER_RETURN", _BASE + " w21 x21 x19 x20", [], 32, None),
    ]
    for static, label, regs, reads, limit, objc in entries:
        _install(target, interpreter, static + slide, label, regs, reads, limit, objc)
