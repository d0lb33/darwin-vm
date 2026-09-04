"""Trace the BackBoardServices data-migration check-in end to end.

This is a read-only LLDB observer.  Shared-cache addresses are from the iOS 27
beta 8 cache.  If ``DVM_BACKBOARDD_BASE`` is omitted, only the cache-side
client boundaries are installed; that mode can freeze at the first check-in so
the same boot's backboardd PIE base can be measured before the request runs.
"""
import json
import os
import time

import lldb


SLIDE = [0]
CONFIG = {}
HITS = {}
EVENT_DIR = os.environ.get("DVM_PROBE_EVENT_DIR", "")
SUCCESS_LABELS = set(filter(None, os.environ.get(
    "DVM_PROBE_SUCCESS_LABELS", "").split(",")))
STOP_ON_SUCCESS = os.environ.get("DVM_BKS_STOP_ON_SUCCESS", "0") != "0"
BYPASS_MIGRATION_CHECKIN = os.environ.get(
    "DVM_BKS_BYPASS_MIGRATION_CHECKIN", "0") != "0"
PROGNAME_PTR = 0x1e6ef1590
MACH_MSG2_TRAP = 0x237ccfccc
BACKBOARDD_SINGLETON_OFFSET = 0xaa668
BACKBOARDD_THREAD_GROUP = 0


def _reg(frame, name):
    value = frame.FindRegister(name)
    return value.GetValueAsUnsigned() if value.IsValid() else None


def _read(process, address, size):
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    return data if error.Success() else b""


def _u64(process, address):
    data = _read(process, address, 8)
    return int.from_bytes(data, "little") if len(data) == 8 else None


def _cstring(process, address, limit=96):
    if not address:
        return "<null>"
    data = _read(process, address, limit)
    return (data.split(b"\0", 1)[0].decode("ascii", "replace")
            if data else "<read-error>")


def _progname(process):
    pointer = _u64(process, SLIDE[0] + PROGNAME_PTR)
    string = _u64(process, pointer) if pointer else None
    return _cstring(process, string)


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


def on_break(frame, bp_loc, _dict):
    breakpoint = bp_loc.GetBreakpoint()
    cfg = CONFIG[breakpoint.GetID()]
    process = frame.GetThread().GetProcess()
    name = _progname(process)
    # PIE addresses are shared across unrelated address spaces. A hit at a
    # backboardd offset in e.g. fairplayd must neither consume the hit budget
    # nor report a successful migration return (DISPLAY_NATIVE_R2, bp 50).
    allowed = cfg.get("allowed")
    if allowed is None and cfg["label"].startswith(("BKD_", "BKM_")):
        allowed = ("backboardd",)
    if allowed and name not in allowed:
        return False
    hit = HITS.get(cfg["label"], 0) + 1
    HITS[cfg["label"]] = hit
    registers = {name: _reg(frame, name) for name in cfg["regs"]}
    payload = {
        "address": cfg["address"],
        "hit": hit,
        "label": cfg["label"],
        "progname": name,
        "registers": registers,
        "thread": frame.GetThread().GetThreadID(),
        "time": time.time(),
    }
    if (BYPASS_MIGRATION_CHECKIN and
            cfg["label"] == "BKS_CLIENT_WAIT_BRANCH" and
            payload["progname"] == "SpringBoard" and
            registers.get("x22") == 1):
        applied = frame.FindRegister("x22").SetValueFromCString("0")
        payload["diagnostic_override"] = {
            "applied": bool(applied),
            "from": 1,
            "purpose": "select public bypass-migration check-in path",
            "to": 0,
        }
        _write_event("diagnostic.BKS_CLIENT_WAIT_BRANCH_BYPASS.json", payload)
    for name, base_reg, offset, size in cfg["reads"]:
        base = _reg(frame, base_reg)
        payload[name] = (_read(process, base + offset, size).hex()
                         if base is not None else "<invalid-base>")
    print("=== %s hit=%d/%d t=%.3f ===" %
          (cfg["label"], hit, cfg["limit"], payload["time"]))
    print("TRACE_JSON " + json.dumps(payload, sort_keys=True))
    _write_event("progress.%s.json" % cfg["label"], payload)
    success = cfg["label"] in SUCCESS_LABELS
    if success:
        _write_event("success.%s.json" % cfg["label"], payload)
    if hit >= cfg["limit"]:
        breakpoint.SetEnabled(False)
        print("bounded-disabled breakpoint=%d after=%d" %
              (breakpoint.GetID(), hit))
    return success and STOP_ON_SUCCESS


def on_backboardd_context(frame, bp_loc, _dict):
    """Stop once in backboardd's address space and expose its check-in state.

    User virtual addresses are only readable through QEMU's gdbstub while the
    vCPU is executing that process.  The system-wide mach_msg2 trap provides a
    cheap anchor; the kernel thread-group check rejects every other process
    with one read before touching backboardd's userspace globals.
    """
    process = frame.GetThread().GetProcess()
    thread_pointer = _reg(frame, "tpidr_el1")
    group = _u64(process, thread_pointer + 0x290) if thread_pointer else None
    if group != BACKBOARDD_THREAD_GROUP:
        return False
    base = CONFIG[bp_loc.GetBreakpoint().GetID()]["backboardd_base"]
    sentinel = _u64(process, base + BACKBOARDD_SINGLETON_OFFSET)
    state = _read(process, sentinel + 0x20, 0x60) if sentinel else b""
    payload = {
        "address": _reg(frame, "pc"),
        "backboardd_base": base,
        "label": "BKD_CONTEXT_ANCHOR",
        "sentinel": sentinel,
        "sentinel_state": state.hex(),
        "thread_group": group,
        "thread_pointer": thread_pointer,
        "time": time.time(),
    }
    print("=== BKD_CONTEXT_ANCHOR sentinel=%s blocked=%s ===" %
          (("0x%x" % sentinel) if sentinel else "<unreadable>",
           ("0x%02x" % state[0x30]) if len(state) > 0x30 else "<unreadable>"))
    print("TRACE_JSON " + json.dumps(payload, sort_keys=True))
    _write_event("success.BKD_CONTEXT_ANCHOR.json", payload)
    bp_loc.GetBreakpoint().SetEnabled(False)
    return True


def on_backboardd_bootstrap(frame, bp_loc, _dict):
    """Capture a fresh boot's backboardd thread group before shell check-in."""
    process = frame.GetThread().GetProcess()
    if _progname(process) != "backboardd":
        return False
    thread_pointer = _reg(frame, "tpidr_el1")
    group = _u64(process, thread_pointer + 0x290) if thread_pointer else None
    payload = {
        "address": _reg(frame, "pc"),
        "label": "BKD_BOOTSTRAP_ANCHOR",
        "progname": "backboardd",
        "thread_group": group,
        "thread_pointer": thread_pointer,
        "time": time.time(),
    }
    print("=== BKD_BOOTSTRAP_ANCHOR thread_group=%s ===" %
          (("0x%x" % group) if group else "<unreadable>"))
    print("TRACE_JSON " + json.dumps(payload, sort_keys=True))
    _write_event("success.BKD_BOOTSTRAP_ANCHOR.json", payload)
    bp_loc.GetBreakpoint().SetEnabled(False)
    return False


def _install(target, interpreter, address, label, regs, reads=(), limit=8):
    before = target.GetNumBreakpoints()
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint set --address 0x%x" % address,
                              result)
    print(result.GetOutput() or result.GetError())
    if target.GetNumBreakpoints() != before + 1:
        raise RuntimeError("failed breakpoint %s at 0x%x" % (label, address))
    breakpoint = target.GetBreakpointAtIndex(before)
    CONFIG[breakpoint.GetID()] = {
        "address": address,
        "label": label,
        "limit": limit,
        "reads": reads,
        "regs": ("pc lr sp tpidr_el1 " + regs).split(),
    }
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand(
        "breakpoint command add -F bks_checkin_callbacks.on_break %d" %
        breakpoint.GetID(), result)
    if not result.Succeeded():
        raise RuntimeError("callback attach failed for %s: %s" %
                           (label, result.GetError()))
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint command list %d" %
                              breakpoint.GetID(), result)
    print("COMMAND_LIST_PROOF id=%d label=%s address=0x%x\n%s" %
          (breakpoint.GetID(), label, address,
           result.GetOutput() or result.GetError()))


def install_context_anchor(debugger, slide, backboardd_base, thread_group):
    """Install the one-shot backboardd address-space anchor on a live guest."""
    global BACKBOARDD_THREAD_GROUP
    SLIDE[0] = int(slide)
    BACKBOARDD_THREAD_GROUP = int(thread_group)
    target = debugger.GetSelectedTarget()
    address = int(slide) + MACH_MSG2_TRAP
    breakpoint = target.BreakpointCreateByAddress(address)
    if not breakpoint.IsValid() or breakpoint.GetNumLocations() != 1:
        raise RuntimeError("failed backboardd context anchor at 0x%x" % address)
    breakpoint.SetScriptCallbackFunction(
        "bks_checkin_callbacks.on_backboardd_context")
    # This is a system-wide syscall site.  Never halt on the thousands of
    # non-backboardd hits; the matching callback writes all required state.
    breakpoint.SetAutoContinue(True)
    CONFIG[breakpoint.GetID()] = {
        "backboardd_base": int(backboardd_base),
        "label": "BKD_CONTEXT_ANCHOR",
    }
    print("COMMAND_LIST_PROOF id=%d label=BKD_CONTEXT_ANCHOR address=0x%x "
          "backboardd_base=0x%x thread_group=0x%x" %
          (breakpoint.GetID(), address, int(backboardd_base),
           BACKBOARDD_THREAD_GROUP))
    return breakpoint.GetID()


def _install_bootstrap_anchor(target, slide):
    address = int(slide) + MACH_MSG2_TRAP
    breakpoint = target.BreakpointCreateByAddress(address)
    if not breakpoint.IsValid() or breakpoint.GetNumLocations() != 1:
        raise RuntimeError("failed backboardd bootstrap anchor at 0x%x" %
                           address)
    breakpoint.SetScriptCallbackFunction(
        "bks_checkin_callbacks.on_backboardd_bootstrap")
    breakpoint.SetAutoContinue(True)
    print("COMMAND_LIST_PROOF id=%d label=BKD_BOOTSTRAP_ANCHOR address=0x%x" %
          (breakpoint.GetID(), address))
    return breakpoint.GetID()


# Unslid shared-cache sites in BackBoardServices.
CACHE_SITES = [
    (0x18a1474a8, "BKS_CLIENT_ENTRY", "x0 x1 x2", (), 8),
    (0x18a147598, "BKS_CLIENT_WAIT_BRANCH", "x20 x21 x22", (), 8),
    (0x18a1476a0, "BKS_CLIENT_REMOTE_CALL", "x0 x1 x2 x16", (), 8),
    (0x18a1476a4, "BKS_CLIENT_REMOTE_RETURN", "x0 x20 x24", (), 8),
    (0x18a147d6c, "BKS_CLIENT_COMPLETION", "x0 x1", (), 8),
]

# Static backboardd offsets.  +0x50 on the sentinel is systemAppBlocked and
# +0x48 is the dictionary retaining completions while that flag is asserted.
BACKBOARDD_SITES = [
    (0x164d4, "BKD_MIGRATION_LISTENER", "x0 x1 x2 x3", (), 8),
    (0x16610, "BKD_CONFIGURE_CONNECTION", "x0 x1", (), 8),
    (0x41944, "BKD_DATA_MIGRATOR_COMPLETE", "x0", (("sentinel", "x0", 0x20, 0x60),), 4),
    (0x433b4, "BKD_CHECKIN_AFTER_MIGRATION", "x0 x1 x2", (("sentinel", "x0", 0x20, 0x60),), 8),
    (0x43274, "BKD_LOCK_CHECKIN", "x0 x1 x2", (("sentinel", "x0", 0x20, 0x60),), 8),
    (0x42ffc, "BKD_COMPLETE_CHECKIN", "x0 x1 x2 x3", (("sentinel", "x0", 0x20, 0x60),), 8),
    (0x430b4, "BKD_CHECKIN_DECISION", "x19 x20 x21 x22", (("sentinel", "x19", 0x20, 0x60),), 8),
    (0x43130, "BKD_CHECKIN_PENDING", "x19 x20 x21 x22 x23", (("sentinel", "x19", 0x20, 0x60),), 8),
    (0x431d8, "BKD_CHECKIN_IMMEDIATE", "x19 x20 x21 x22", (("sentinel", "x19", 0x20, 0x60),), 8),
    (0x4342c, "BKD_CHECKIN_METHOD_RETURN", "x0 x19", (("sentinel", "x19", 0x20, 0x60),), 8),
]


def install(debugger, slide):
    SLIDE[0] = slide
    raw_base = os.environ.get("DVM_BACKBOARDD_BASE")
    backboardd_base = int(raw_base, 0) if raw_base else None
    target = debugger.GetSelectedTarget()
    interpreter = debugger.GetCommandInterpreter()
    for static, label, regs, reads, limit in CACHE_SITES:
        _install(target, interpreter, static + slide, label, regs, reads, limit)
    if backboardd_base is None:
        _install_bootstrap_anchor(target, slide)
    if backboardd_base is not None:
        for offset, label, regs, reads, limit in BACKBOARDD_SITES:
            _install(target, interpreter, backboardd_base + offset, label,
                     regs, reads, limit)
    _write_event("ready", {"backboardd_base": backboardd_base,
                           "breakpoints": len(CONFIG), "slide": slide,
                           "time": time.time()})
    print("BKS_CHECKIN_TRACE_READY slide=0x%x backboardd_base=%s "
          "breakpoints=%d stop_on_success=%d" %
          (slide, ("0x%x" % backboardd_base
                   if backboardd_base is not None else "<cache-only>"),
           len(CONFIG), int(STOP_ON_SUCCESS)))
