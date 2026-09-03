"""Focused, read-only probe for the AFK SMMachine power transition.

The display-off owner is stuck below AppleFirmwareKit's SMMachine transition
executor.  The relevant static addresses are in the iOS 27 bootkc:

* AppleFirmwareKit+0x4937c is the return site in AFK's client state callback;
  its containing function maps (notification, asserted) to states 0/1/2.
* AppleFirmwareKit+0x26f6c requests that state transition.
* AppleFirmwareKit+0x3102c is inside the transition executor that advances
  the SMEvent stage field through 0, 1, 2, and 3.

The module deliberately changes no guest memory or registers.  It imports the
established IOKit selector monitor so selector 0x4f remains a positive control
and emits PROBE_EVENT records for every focused hit.
"""
import os
import time
import lldb

import display_iokit_callbacks


KSLIDE = 0x20000000
CONFIG = {}
RETURNS = {}
HITS = {}


def _reg(frame, name):
    value = frame.FindRegister(name)
    return value.GetValueAsUnsigned() if value.IsValid() else None


def _read(process, addr, size):
    # LLDB rejects an empty read.  Some entry points use x1/x2 for unrelated
    # arguments (the A500 helper is one), so a zero buffer is data, not an
    # instrumentation failure.
    if not addr or size <= 0:
        return b""
    error = lldb.SBError()
    data = process.ReadMemory(addr, size, error)
    return data if error.Success() else b""


def _u64(process, addr):
    data = _read(process, addr, 8)
    return int.from_bytes(data, "little") if len(data) == 8 else None


def _words(process, addr, count=5):
    if not addr:
        return "<null>"
    values = []
    for index in range(count):
        value = _u64(process, addr + index * 8)
        values.append("?" if value is None else "0x%x" % value)
    return ",".join(values)


def _dump_object(process, label, address, offsets):
    fields = []
    for offset in offsets:
        value = _u64(process, address + offset) if address else None
        fields.append("+0x%x=%s" % (offset, "?" if value is None else "0x%x" % value))
    print("%s object=0x%x %s" % (label, address or 0, " ".join(fields)))


def on_return(frame, bp_loc, _dict):
    breakpoint = bp_loc.GetBreakpoint()
    cfg = RETURNS.get(breakpoint.GetID())
    if cfg is None:
        return False
    if _reg(frame, "tpidr_el1") != cfg["tp"]:
        return False
    result = _reg(frame, "x0")
    print("=== %s_RET elapsed=%.3f ===" % (cfg["label"], time.time() - cfg["time"]))
    print("thread=0x%x x0=%s" % (frame.GetThread().GetThreadID(),
          "?" if result is None else "0x%x" % result))
    print("PROBE_EVENT event=%s-return thread=0x%x result=0x%x" %
          (cfg["label"].lower(), frame.GetThread().GetThreadID(), result or 0), flush=True)
    frame.GetThread().GetProcess().GetTarget().BreakpointDelete(breakpoint.GetID())
    del RETURNS[breakpoint.GetID()]
    return False


def on_break(frame, bp_loc, _dict):
    breakpoint = bp_loc.GetBreakpoint()
    cfg = CONFIG[breakpoint.GetID()]
    hit = HITS.get(breakpoint.GetID(), 0) + 1
    HITS[breakpoint.GetID()] = hit
    process = frame.GetThread().GetProcess()
    x0, x1, x2, lr, tp = (_reg(frame, name) for name in ("x0", "x1", "x2", "lr", "tpidr_el1"))
    print("=== %s hit=%d/%d ===" % (cfg["label"], hit, cfg["limit"]))
    print("thread=0x%x tpidr_el1=0x%x pc=0x%x lr=0x%x x0=0x%x x1=0x%x x2=0x%x" %
          (frame.GetThread().GetThreadID(), tp or 0, _reg(frame, "pc") or 0, lr or 0,
           x0 or 0, x1 or 0, x2 or 0))
    if cfg["label"] == "AFK_CLIENT_STATE_EVENT":
        _dump_object(process, "client", x0, (0x110, 0x130, 0x138, 0x140, 0x1a0))
        print("client_words=%s" % _words(process, x0))
    elif cfg["label"] == "AFK_REQUEST_SM_STATE":
        _dump_object(process, "state-client", x0, (0x110, 0x130, 0x138, 0x140))
    elif cfg["label"] == "SMMACHINE_TRANSITION":
        _dump_object(process, "machine", x0, (0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40))
        _dump_object(process, "transition", x1, (0x10, 0x18, 0x20, 0x28, 0x30))
    elif cfg["label"] == "IOMFB_A500":
        if x1 and x2:
            print("A500 bytes=%s" % _read(process, x1, min(x2, 16)).hex())
        else:
            print("A500 buffer=none (x1=0x%x x2=0x%x)" % (x1 or 0, x2 or 0))
    print("PROBE_EVENT event=%s-entry thread=0x%x x0=0x%x x1=0x%x x2=0x%x lr=0x%x" %
          (cfg["label"].lower(), frame.GetThread().GetThreadID(), x0 or 0, x1 or 0, x2 or 0, lr or 0), flush=True)
    if lr and len(RETURNS) < 24:
        target = process.GetTarget()
        return_bp = target.BreakpointCreateByAddress(lr & 0x0000ffffffffffff)
        return_bp.SetScriptCallbackFunction("dcp_sm_machine_callbacks.on_return")
        RETURNS[return_bp.GetID()] = {"label": cfg["label"], "tp": tp, "time": time.time()}
    if hit >= cfg["limit"]:
        breakpoint.SetEnabled(False)
        print("bounded-disabled breakpoint=%d after=%d" % (breakpoint.GetID(), hit))
    return False


def _install(target, interpreter, static, label, limit):
    address = static + KSLIDE
    before = target.GetNumBreakpoints()
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint set --address 0x%x" % address, result)
    if target.GetNumBreakpoints() != before + 1:
        raise RuntimeError("failed breakpoint %s at 0x%x: %s" % (label, address, result.GetError()))
    breakpoint = target.GetBreakpointAtIndex(before)
    CONFIG[breakpoint.GetID()] = {"label": label, "limit": limit}
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint command add -F dcp_sm_machine_callbacks.on_break %d" % breakpoint.GetID(), result)
    if not result.Succeeded():
        raise RuntimeError("callback attach failed for %s: %s" % (label, result.GetError()))
    process = target.GetProcess()
    instructions = _read(process, address, 16).hex()
    print("COMMAND_LIST_PROOF id=%d label=%s address=0x%x" % (breakpoint.GetID(), label, address))
    print("INSTRUCTION_PROOF label=%s bytes=%s" % (label, instructions))


def install(debugger, slide):
    # Retain the validated selector-79 entry/return and watcher protocol.
    # LLDB's ``breakpoint command add -F`` resolves callable names only in its
    # own script namespace; a normal Python import above is not sufficient.
    # Register the established module explicitly before asking it to install.
    result = lldb.SBCommandReturnObject()
    debugger.GetCommandInterpreter().HandleCommand(
        "command script import %s" % os.path.join(os.path.dirname(__file__), "display_iokit_callbacks.py"),
        result)
    if not result.Succeeded():
        raise RuntimeError("could not register display_iokit_callbacks: %s" % result.GetError())
    display_iokit_callbacks.install(debugger, slide)
    target = debugger.GetSelectedTarget()
    interpreter = debugger.GetCommandInterpreter()
    for static, label, limit in (
        (0xfffffff008ba47b0, "AFK_CLIENT_STATE_EVENT", 32),
        (0xfffffff008b82378, "AFK_REQUEST_SM_STATE", 32),
        (0xfffffff008b8c2d0, "SMMACHINE_TRANSITION", 32),
        (0xfffffff00a0d41f0, "IOMFB_A500", 8),
    ):
        _install(target, interpreter, static, label, limit)
