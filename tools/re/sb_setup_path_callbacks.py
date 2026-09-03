"""LLDB callbacks for SpringBoard's first-boot Setup launch path (phase 2).

Phase 1 (setup_gate_callbacks.py, docs/re/setup-launch-runtime.md) proved
BYSetupAssistantNeedsToRun() answers YES to everyone.  This phase instruments
the SpringBoard side: who calls -[SBSetupManager updateInSetupMode], what
reason it sets, whether the return-to-lock-screen transaction chooses Buddy,
whether -[SBApplicationController setupApplication] finds Setup.app, and
whether _SBWorkspaceActivateApplication is ever reached.  Every hit prints
the process name read from libsystem_c's ___progname_pointer, so shared-cache
breakpoints are attributed across processes.

Static VAs are from the iOS 27 beta 8 cache, resolved with `ipsw dyld a2s` /
`ipsw dyld disass --symbol` (derivations in docs/re/setup-launch-runtime.md).

    command script import tools/re/sb_setup_path_callbacks.py
    script sb_setup_path_callbacks.install(lldb.debugger, <slide>)
"""
import json
import os
import time
import lldb


CONFIG = {}
HITS = {}
SLIDE = [0]
CACHE_LO = 0x180000000
CACHE_HI = 0x340000000
PROGNAME_PTR = 0x1e6ef1590      # libsystem_c ___progname_pointer (__DATA_DIRTY.__bss)
EVENT_DIR = os.environ.get("DVM_PROBE_EVENT_DIR", "")
SUCCESS_LABELS = set(filter(None, os.environ.get("DVM_PROBE_SUCCESS_LABELS", "").split(",")))


def _write_event(name, payload):
    if not EVENT_DIR:
        return
    os.makedirs(EVENT_DIR, exist_ok=True)
    path = os.path.join(EVENT_DIR, name)
    temporary = "%s.%d.tmp" % (path, os.getpid())
    with open(temporary, "w") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


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


def _cstring(process, address, limit=128):
    if not address:
        return "<null>"
    data = _read(process, address, limit)
    if data is None:
        return "<read-error>"
    return data.split(b"\0", 1)[0].decode("ascii", "replace")


def progname(process):
    """__NSGetProgname() returns *___progname_pointer; getprogname() derefs once more."""
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
    print("=== %s hit=%d/%d t=%.3f ===" % (cfg["label"], hit, cfg["limit"], time.time()))
    print("progname=%s breakpoint=%d thread=0x%x" %
          (progname(process), bp_id, thread.GetThreadID()))
    values = []
    for name in cfg["regs"]:
        value = _reg(frame, name)
        values.append("%s=%s" % (name, "<invalid>" if value is None else "0x%x" % value))
    print("registers " + " ".join(values))
    st = _static(lr)
    print("lr_static=%s" % ("0x%x" % st if st is not None else "<outside-cache>"))
    print("code@0x%x=%s" % (pc, _hex(_read(process, pc, 8))))
    if cfg["label"] in SUCCESS_LABELS:
        _write_event("success.%s.json" % cfg["label"],
                     {"label": cfg["label"], "time": time.time(), "hit": hit,
                      "thread": thread.GetThreadID()})
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
    if hit >= cfg["limit"]:
        bp.SetEnabled(False)
        print("bounded-disabled breakpoint=%d after=%d" % (bp_id, hit))
    return False


def _install(target, interpreter, address, label, regs, reads, limit):
    before = target.GetNumBreakpoints()
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint set --address 0x%x" % address, result)
    print(result.GetOutput() or result.GetError())
    if target.GetNumBreakpoints() != before + 1:
        raise RuntimeError("failed breakpoint %s at 0x%x" % (label, address))
    bp = target.GetBreakpointAtIndex(before)
    bp_id = bp.GetID()
    CONFIG[bp_id] = {"label": label, "regs": regs.split(), "reads": reads, "limit": limit}
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand(
        "breakpoint command add -F sb_setup_path_callbacks.on_break %d" % bp_id, result)
    if not result.Succeeded():
        raise RuntimeError("callback attach failed for %d: %s" % (bp_id, result.GetError()))
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint command list %d" % bp_id, result)
    print("COMMAND_LIST_PROOF id=%d label=%s address=0x%x\n%s" %
          (bp_id, label, address, result.GetOutput() or result.GetError()))


_BASE = "pc lr sp tpidr_el1"

# (static VA, label, extra regs, reads, hit limit)
ENTRIES = [
    # -[SpringBoard applicationDidFinishLaunching:] and its updateInSetupMode return (+7076)
    (0x2244d0ba4, "SB_ADFL_ENTRY", "x0 x2", [], 8),
    (0x2244d274c, "SB_ADFL_UPDATE_RET", "w0", [], 4),
    # -[SpringBoard _finalizeStartupAfterScenesDidConnect:] and its updateInSetupMode return (+264)
    (0x2244d8cf4, "SB_FINALIZE_ENTRY", "x0 x2", [], 4),
    (0x2244d8e00, "SB_FINALIZE_UPDATE_RET", "w0", [], 4),
    # -[SBSetupManager updateInSetupMode]: entry, reason-0 tail call, reason call (x2)
    (0x2246d37f0, "SB_UPDATE_ENTRY", "x0", [("migrating_byte", "x0", 0x41, 1)], 24),
    (0x2246d3864, "SB_UPDATE_REASON0_TAIL", "x0 x2", [], 24),
    (0x2246d3964, "SB_UPDATE_SET_REASON_CALL", "x0 x2 w21", [], 24),
    # -[SBSetupManager _setSetupRequiredReason:]: x2 = reason; +0x38 branch compares old/new inSetupMode
    (0x2246d3adc, "SB_SET_REASON_ENTRY", "x0 x2", [("old_reason", "x0", 0x38, 8)], 24),
    (0x2246d3b14, "SB_SET_REASON_CHANGED_BRANCH", "w21 w0", [], 24),
    # -[SBMainWorkspace _selectTransactionForReturningToTheLockScreenAndForceToBuddy:] (x2 = BOOL)
    (0x224478298, "SB_RETURN_TO_LOCK_ENTRY", "x0 x2", [], 8),
    (0x22447835c, "SB_FORCEBUDDY_BLOCK_ENTRY", "x0", [("force_to_buddy", "x0", 0x28, 1)], 8),
    (0x2244783a4, "SB_FORCEBUDDY_ISINSETUP_RET", "w0 x19", [], 8),
    (0x2244783b0, "SB_FORCEBUDDY_UPDATE_RET", "w0 x19", [], 8),
    (0x2244783c0, "SB_FORCEBUDDY_LAUNCH_PATH", "x0", [], 8),
    # [applicationController applicationWithBundleIdentifier:SBBuddyBundleIdentifier] return: x0 = Setup app or nil
    (0x2244783d4, "SB_FORCEBUDDY_APP_LOOKUP_RET", "x0", [("app_isa", "x0", 0, 8)], 8),
    (0x224478414, "SB_FORCEBUDDY_LOCKSCREEN_PATH", "x0", [], 8),
    # -[SBToAppsWorkspaceTransaction _didComplete]: updateInSetupMode ret, setupApplication ret, activate
    (0x2242e47ec, "SB_TOAPPS_DIDCOMPLETE_ENTRY", "x0", [], 8),
    (0x2242e48d0, "SB_TOAPPS_UPDATE_RET", "w0", [], 8),
    (0x2242e4a5c, "SB_TOAPPS_SETUPAPP_RET", "x0", [], 8),
    (0x2242e4a64, "SB_TOAPPS_ACTIVATE_CALL", "x0", [], 8),
    # __SBWorkspaceCanLaunchApplication and its updateInSetupMode return (+384)
    (0x22430442c, "SB_CANLAUNCH_ENTRY", "x0 x1", [], 16),
    (0x2243045b0, "SB_CANLAUNCH_UPDATE_RET", "w0", [], 16),
    # -[SBMainWorkspace _validateRequestToOpenApplication:options:origin:error:] updateInSetupMode ret
    (0x224312dac, "SB_VALIDATE_OPEN_UPDATE_RET", "w0", [], 16),
    # _SBWorkspaceActivateApplication entry: any caller
    (0x22433ff18, "SB_ACTIVATE_ENTRY", "x0 x1", [("app_isa", "x0", 0, 8)], 16),
    # -[SBMainWorkspace _handleSetupExited:]: Setup launched and died
    (0x224479448, "SB_HANDLE_SETUP_EXITED", "x0 x2", [], 8),
    # bricked-device path (phase 1 site), kept as a control
    (0x224c4fb78, "SB_BRICKED_ACTIVATE_CALL", "x0", [], 8),
    # SetupAssistant gate, attributed by progname this time
    (0x1cac11c18, "BY_NEEDS_ENTRY", "x0", [], 64),
    (0x1cac11cf0, "BY_NEEDS_RETURN", "w0", [], 64),
    (0x1cac23394, "BY_PREPARE_LAUNCH_SENTINEL", "x0 x1", [], 8),
    # who dies: libsystem_c abort() and the __exit syscall wrapper, named by progname
    (0x187f727a4, "LIBC_ABORT", "x0", [], 16),
    (0x237ce21a4, "SYS_EXIT", "w0", [], 48),
]

# -[SBApplicationController setupApplication] is a tail call to
# applicationWithBundleIdentifier:(SBBuddyBundleIdentifier) at 0x2243bf92c, so
# only its entry is breakable; its result is read at the callers' return sites.
SETUP_APPLICATION = (0x2243bf920, None)


def install(debugger, slide):
    SLIDE[0] = slide
    target = debugger.GetSelectedTarget()
    interpreter = debugger.GetCommandInterpreter()
    entries = list(ENTRIES)
    entries.append((SETUP_APPLICATION[0], "SB_SETUPAPP_ENTRY", "x0 x1", [], 8))
    for static, label, regs, reads, limit in entries:
        _install(target, interpreter, static + slide, label, _BASE + " " + regs, reads, limit)
    _write_event("ready", {"time": time.time(), "breakpoints": len(entries)})
