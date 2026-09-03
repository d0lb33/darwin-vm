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
STAGE2_PENDING = {}
STAGE2_SEQUENCE = {}
TAIL_TARGETS = {}
TAIL_TARGET_BY_ADDRESS = {}


def _reg(frame, name):
    value = frame.FindRegister(name)
    return value.GetValueAsUnsigned() if value.IsValid() else None


def _kptr(value):
    """Restore XNU's kernel sign extension from a PAC-stripped pointer."""
    if not value:
        return 0
    return 0xfffffff000000000 | (value & 0xffffffffff)


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


def _dump_stage2_operation(process, operation):
    """Print the operation object and dispatch table used by BLRAA x9, x8."""
    _dump_object(process, "stage2-operation", operation,
                 (0x0, 0x8, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40))
    vtable = _u64(process, operation) if operation else None
    print("stage2-operation-vtable=0x%x words=%s" %
          (vtable or 0, _words(process, vtable, 8)))


def _dump_tail_object(process, dynamic):
    """Dump the object supplying the stage-2 tail-call virtual method."""
    _dump_object(process, "tail-dynamic", dynamic,
                 (0x0, 0x8, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40,
                  0x88, 0x100, 0x108, 0x110, 0x118, 0x150))
    vtable_raw = _u64(process, dynamic) if dynamic else None
    vtable = _kptr(vtable_raw)
    entry = _u64(process, vtable + 0x100) if vtable else None
    print("tail-dynamic-vtable raw=0x%x canonical=0x%x words=%s" %
          (vtable_raw or 0, vtable, _words(process, vtable, 0x21)))
    print("tail-dynamic-vtable-entry+0x100 raw=0x%x canonical=0x%x" %
          (entry or 0, _kptr(entry)))


def _install_tail_target(process, target_address, source):
    """Install the read-only entry witness for a just-resolved tail target."""
    target = process.GetTarget()
    existing = TAIL_TARGET_BY_ADDRESS.get(target_address)
    if existing is not None:
        return existing
    before = target.GetNumBreakpoints()
    breakpoint = target.BreakpointCreateByAddress(target_address)
    if target.GetNumBreakpoints() != before + 1:
        raise RuntimeError("failed dynamic tail target breakpoint at 0x%x" % target_address)
    breakpoint.SetScriptCallbackFunction("dcp_sm_machine_callbacks.on_tail_target")
    TAIL_TARGETS[breakpoint.GetID()] = {"address": target_address, "source": source}
    TAIL_TARGET_BY_ADDRESS[target_address] = breakpoint.GetID()
    print("DYNAMIC_BREAKPOINT_PROOF id=%d label=SMMACHINE_TAIL_TARGET address=0x%x "
          "source-thread=0x%x" % (breakpoint.GetID(), target_address, source["thread"]))
    return breakpoint.GetID()


def on_tail_boundary(frame, bp_loc, _dict):
    """Resolve and witness the virtual tail call in AFK's stage-2 action."""
    breakpoint = bp_loc.GetBreakpoint()
    hit = HITS.get(breakpoint.GetID(), 0) + 1
    HITS[breakpoint.GetID()] = hit
    process = frame.GetThread().GetProcess()
    thread = frame.GetThread().GetThreadID()
    tp = _reg(frame, "tpidr_el1")
    x0, x1, x2, x16, x17 = (_reg(frame, name) for name in
                             ("x0", "x1", "x2", "x16", "x17"))
    target = _kptr(x16)
    if not target:
        raise RuntimeError("tail boundary has no authenticated x16 target")
    print("=== SMMACHINE_TAIL_BOUNDARY hit=%d/8 ===" % hit)
    print("thread=0x%x tpidr_el1=0x%x x0(dynamic)=0x%x x1(context)=0x%x "
          "w2=0x%x x16(auth-target)=0x%x x16-canonical=0x%x x17(pac-context)=0x%x" %
          (thread, tp or 0, x0 or 0, x1 or 0, (x2 or 0) & 0xffffffff,
           x16 or 0, target, x17 or 0))
    _dump_tail_object(process, x0)
    dynamic_id = _install_tail_target(process, target,
                                      {"thread": thread, "tp": tp, "dynamic": x0,
                                       "context": x1, "w2": (x2 or 0) & 0xffffffff})
    print("PROBE_EVENT event=smmachine-tail-boundary thread=0x%x dynamic=0x%x "
          "context=0x%x w2=0x%x target=0x%x breakpoint=%d" %
          (thread, x0 or 0, x1 or 0, (x2 or 0) & 0xffffffff, target, dynamic_id),
          flush=True)
    if hit >= 8:
        breakpoint.SetEnabled(False)
        print("bounded-disabled breakpoint=%d after=%d" % (breakpoint.GetID(), hit))
    return False


def on_tail_target(frame, bp_loc, _dict):
    """Positive control: the resolved tail target was actually entered."""
    breakpoint = bp_loc.GetBreakpoint()
    hit = HITS.get(breakpoint.GetID(), 0) + 1
    HITS[breakpoint.GetID()] = hit
    cfg = TAIL_TARGETS[breakpoint.GetID()]
    process = frame.GetThread().GetProcess()
    thread = frame.GetThread().GetThreadID()
    tp = _reg(frame, "tpidr_el1")
    x0, x1, x2, lr = (_reg(frame, name) for name in ("x0", "x1", "x2", "lr"))
    print("=== SMMACHINE_TAIL_TARGET hit=%d/8 ===" % hit)
    print("thread=0x%x tpidr_el1=0x%x pc=0x%x lr=0x%x x0=0x%x x1=0x%x w2=0x%x" %
          (thread, tp or 0, _reg(frame, "pc") or 0, lr or 0, x0 or 0, x1 or 0,
           (x2 or 0) & 0xffffffff))
    _dump_tail_object(process, x0)
    print("PROBE_EVENT event=smmachine-tail-target-entry thread=0x%x target=0x%x "
          "dynamic=0x%x context=0x%x w2=0x%x" %
          (thread, cfg["address"], x0 or 0, x1 or 0, (x2 or 0) & 0xffffffff),
          flush=True)
    if hit >= 8:
        breakpoint.SetEnabled(False)
        print("bounded-disabled breakpoint=%d after=%d" % (breakpoint.GetID(), hit))
    return False


def on_stage2_pre(frame, bp_loc, _dict):
    """Witness the exact indirect stage-2 operation before BLRAA x9, x8."""
    breakpoint = bp_loc.GetBreakpoint()
    hit = HITS.get(breakpoint.GetID(), 0) + 1
    HITS[breakpoint.GetID()] = hit
    process = frame.GetThread().GetProcess()
    thread = frame.GetThread().GetThreadID()
    tp = _reg(frame, "tpidr_el1")
    x0, x1, x8, x9 = (_reg(frame, name) for name in ("x0", "x1", "x8", "x9"))
    target = _kptr(x9)
    sequence = STAGE2_SEQUENCE.get(tp, 0) + 1
    STAGE2_SEQUENCE[tp] = sequence
    print("=== SMMACHINE_STAGE2_PRE hit=%d/8 sequence=%d ===" % (hit, sequence))
    print("thread=0x%x tpidr_el1=0x%x x0(operation)=0x%x x1(context)=0x%x "
          "x8=0x%x x9(auth-target)=0x%x x9-canonical=0x%x" %
          (thread, tp or 0, x0 or 0, x1 or 0, x8 or 0, x9 or 0, target))
    _dump_stage2_operation(process, x0)
    pending = STAGE2_PENDING.setdefault(tp, [])
    pending.append({"sequence": sequence, "operation": x0, "context": x1,
                    "target": target, "time": time.time()})
    print("PROBE_EVENT event=smmachine-stage2-pre sequence=%d thread=0x%x "
          "operation=0x%x context=0x%x target=0x%x" %
          (sequence, thread, x0 or 0, x1 or 0, target), flush=True)
    if hit >= 8:
        breakpoint.SetEnabled(False)
        print("bounded-disabled breakpoint=%d after=%d" % (breakpoint.GetID(), hit))
    return False


def on_stage2_return(frame, bp_loc, _dict):
    """Witness the instruction immediately after the stage-2 indirect call."""
    breakpoint = bp_loc.GetBreakpoint()
    hit = HITS.get(breakpoint.GetID(), 0) + 1
    HITS[breakpoint.GetID()] = hit
    thread = frame.GetThread().GetThreadID()
    tp = _reg(frame, "tpidr_el1")
    pending = STAGE2_PENDING.get(tp, [])
    call = pending.pop(0) if pending else None
    if call:
        print("=== SMMACHINE_STAGE2_RETURN hit=%d/8 sequence=%d elapsed=%.3f ===" %
              (hit, call["sequence"], time.time() - call["time"]))
        print("thread=0x%x tpidr_el1=0x%x operation=0x%x context=0x%x target=0x%x" %
              (thread, tp or 0, call["operation"] or 0, call["context"] or 0,
               call["target"]))
        print("PROBE_EVENT event=smmachine-stage2-return sequence=%d thread=0x%x "
              "target=0x%x" % (call["sequence"], thread, call["target"]), flush=True)
    else:
        print("=== SMMACHINE_STAGE2_RETURN hit=%d/8 without-pre ===" % hit)
        print("thread=0x%x tpidr_el1=0x%x" % (thread, tp or 0))
        print("PROBE_EVENT event=smmachine-stage2-return-without-pre thread=0x%x" %
              thread, flush=True)
    if hit >= 8:
        breakpoint.SetEnabled(False)
        print("bounded-disabled breakpoint=%d after=%d" % (breakpoint.GetID(), hit))
    return False


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
        return_bp = target.BreakpointCreateByAddress(_kptr(lr))
        return_bp.SetScriptCallbackFunction("dcp_sm_machine_callbacks.on_return")
        RETURNS[return_bp.GetID()] = {"label": cfg["label"], "tp": tp, "time": time.time()}
    if hit >= cfg["limit"]:
        breakpoint.SetEnabled(False)
        print("bounded-disabled breakpoint=%d after=%d" % (breakpoint.GetID(), hit))
    return False


def _install(target, interpreter, static, label, limit, callback="on_break"):
    address = static + KSLIDE
    before = target.GetNumBreakpoints()
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint set --address 0x%x" % address, result)
    if target.GetNumBreakpoints() != before + 1:
        raise RuntimeError("failed breakpoint %s at 0x%x: %s" % (label, address, result.GetError()))
    breakpoint = target.GetBreakpointAtIndex(before)
    CONFIG[breakpoint.GetID()] = {"label": label, "limit": limit}
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint command add -F dcp_sm_machine_callbacks.%s %d" %
                              (callback, breakpoint.GetID()), result)
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
    for static, label, limit, callback in (
        (0xfffffff008ba47b0, "AFK_CLIENT_STATE_EVENT", 32, "on_break"),
        (0xfffffff008b82378, "AFK_REQUEST_SM_STATE", 32, "on_break"),
        (0xfffffff008b8c2d0, "SMMACHINE_TRANSITION", 32, "on_break"),
        (0xfffffff00a0d41f0, "IOMFB_A500", 8, "on_break"),
        # 0x31028/0x3102c bracket the BLRAA x9, x8 stage-2 action.  The
        # post-call site is the non-heuristic return witness for each action.
        (0xfffffff008b8c4e8, "SMMACHINE_STAGE2_PRE", 8, "on_stage2_pre"),
        (0xfffffff008b8c4ec, "SMMACHINE_STAGE2_RETURN", 8, "on_stage2_return"),
        # The stage-2 action ends in this authenticated virtual tail call.
        # Resolve x16 here rather than guessing the dynamic object's vtable.
        (0xfffffff008b8209c, "SMMACHINE_TAIL_BOUNDARY", 8, "on_tail_boundary"),
    ):
        _install(target, interpreter, static, label, limit, callback)
