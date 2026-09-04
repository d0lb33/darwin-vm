"""Bounded same-boot tracer for the blocked DCP AppleFirmwareKit SMMachine.

The trace arms only when the AFK state-1 request reaches the transition whose
destination is the client's state-1 object.  From the executor's stage-2
operation it discovers authenticated indirect targets at runtime, installs
entry/return witnesses, and recursively scans only those entered targets for
further indirect calls.  Every callback is read-only.

By default the tracer stops at the established kernel sleep primitive and at
the stage-2 return.  Either boundary can be made transparent for a follow-on
trace in the same boot.  It does not infer a firmware message from an absent
callback or modify guest state.  ``TRACE_JSON`` records are converted to a
line-numbered report by ``sm_dynamic_report.py``.
"""
import json
import os
import re
import sys
import time

import lldb

import display_iokit_callbacks


KSLIDE = 0x20000000
BOOTKC = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../firmware/bootkc"))
MAX_HOPS = int(os.environ.get("DVM_SM_TRACE_MAX_HOPS", "16"), 0)
MAX_BREAKPOINTS = int(os.environ.get("DVM_SM_TRACE_MAX_BREAKPOINTS", "96"), 0)
MAX_SECONDS = float(os.environ.get("DVM_SM_TRACE_MAX_SECONDS", "45"))
SCAN_INSTRUCTIONS = int(os.environ.get("DVM_SM_TRACE_SCAN_INSTRUCTIONS", "128"), 0)
STOP_ON_WAIT = os.environ.get("DVM_SM_TRACE_STOP_ON_WAIT", "1") != "0"
STOP_ON_STAGE2_RETURN = os.environ.get(
    "DVM_SM_TRACE_STOP_ON_STAGE2_RETURN", "1") != "0"

CLIENT_POINTER_FIELDS = {
    "command_gate": 0x110,
    "completion_event": 0x118,
    "state_starting": 0x120,
    "state_ready": 0x128,
    "state_rtbuddy_on": 0x130,
    "state_rtbuddy_off": 0x138,
    "state_rtbuddy_sleep": 0x140,
    "state_no_interfaces": 0x148,
    "endpoint_field_0x178": 0x178,
    "endpoint_field_0x180": 0x180,
    "endpoint_field_0x1a0": 0x1a0,
}

# AFKEPKextV2 creates these named SMMachine event blocks in its initializer.
# The addresses are the authenticated block targets installed at
# 0xfffffff008b7fc30..0xfffffff008b7fd24.
ACTION_NAMES = {
    0xfffffff008b81e34: "config",
    0xfffffff008b81e50: "on",
    0xfffffff008b81fe8: "off",
}

# These slots are independently established by the instruction stream.  The
# fallback inference below handles newly discovered vtable loads.
KNOWN_SLOTS = {
    0xfffffff008b8209c: 0x100,
    0xfffffff00b23f4e0: 0x98,
    0xfffffff00b23f52c: 0xf0,
}

CONFIG = {}
SITES = {}
TARGETS = {}
TARGET_BY_ADDRESS = {}
RETURNS = {}
RETURN_BY_ADDRESS = {}
EXPECTED = {}
PENDING_RETURNS = {}
SCANNED = set()
TRACE = {
    "candidate_tp": None,
    "candidate_thread": None,
    "afk_client": None,
    "requested_state": None,
    "armed": False,
    "stopped": False,
    "started": None,
    "machine": None,
    "transition": None,
    "gate": None,
    "context": None,
    "owner": None,
    "lock": None,
    "wait_event": None,
    "stage2_returned": False,
    "hops": 0,
    "dynamic_breakpoints": 0,
    "task_mode_hits": 0,
}


def _reg(frame, name):
    value = frame.FindRegister(name)
    return value.GetValueAsUnsigned() if value.IsValid() else None


def _kptr(value):
    if not value:
        return 0
    return 0xfffffff000000000 | (value & 0xffffffffff)


def _read(process, address, size):
    if not address or size <= 0:
        return b""
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    return data if error.Success() else b""


def _u64(process, address):
    data = _read(process, address, 8)
    return int.from_bytes(data, "little") if len(data) == 8 else None


def _words(process, base, offsets):
    if not base:
        return {}
    return {"+0x%x" % offset: _u64(process, base + offset)
            for offset in offsets}


def _client_snapshot(process, client):
    if not client:
        return None
    pointers = {name: _u64(process, client + offset)
                for name, offset in CLIENT_POINTER_FIELDS.items()}
    task = client + 0x88
    return {
        "address": client,
        "vtable_raw": _u64(process, client),
        "state_task": task,
        "state_task_vtable_raw": _u64(process, task),
        "pointers": pointers,
    }


def _task_snapshot(process, task):
    """Capture the StateTask's compact live state without interpreting it away."""
    if not task:
        return None
    state = _read(process, task + 0x48, 0x10)
    begin = _u64(process, task + 0x30)
    end = _u64(process, task + 0x38)
    descriptors = []
    if begin and end and begin <= end and end - begin <= 0x400:
        for address in range(begin, end, 0x20):
            raw = _read(process, address, 0x20)
            if len(raw) != 0x20:
                break
            descriptors.append({
                "address": address,
                "callback_raw": int.from_bytes(raw[0:8], "little"),
                "context_offset": int.from_bytes(raw[8:16], "little"),
                "event": int.from_bytes(raw[16:24], "little"),
                "desired_state": raw[24],
                "task_type": raw[25],
                "flags": raw[26],
                "raw": raw.hex(),
            })
    return {
        "address": task,
        "state_0x48_0x57": state.hex() if state else None,
        "state_bytes": ({"0x48": state[0], "0x49": state[1],
                         "0x4a": state[2], "0x4b": state[3],
                         "0x4c": state[4], "0x4d": state[5]}
                        if state and len(state) >= 6 else None),
        "descriptor_begin": begin,
        "descriptor_end": end,
        "descriptor_capacity": _u64(process, task + 0x40),
        "descriptors": descriptors,
    }


def _state_name(snapshot, value):
    if not snapshot or not value:
        return None
    for name, pointer in snapshot["pointers"].items():
        if name.startswith("state_") and pointer == value:
            return name.removeprefix("state_")
    return None


def _operation_snapshot(process, operation):
    if not operation:
        return None
    words = _words(process, operation, range(0, 0x48, 8))
    raw_target = words.get("+0x10")
    return {
        "address": operation,
        "words": words,
        "target": _address_meta(_kptr(raw_target)) if raw_target else None,
    }


def _transition_snapshot(process, transition, client_snapshot=None):
    if not transition:
        return None
    words = _words(process, transition, (0x10, 0x18, 0x20, 0x28, 0x30))
    return {
        "address": transition,
        "words": words,
        "source_state": _state_name(client_snapshot, words.get("+0x18")),
        "destination_state": _state_name(client_snapshot, words.get("+0x20")),
    }


def _load_ranges():
    try:
        if os.path.dirname(__file__) not in sys.path:
            sys.path.insert(0, os.path.dirname(__file__))
        import kc_text_map
        return kc_text_map.load_map(BOOTKC)
    except Exception as error:
        print("TRACE_WARNING map-load-failed error=%s" % error, flush=True)
        return []


RANGES = _load_ranges()


def _address_meta(runtime):
    static = runtime - KSLIDE if runtime and runtime >= 0xfffffff020000000 else runtime
    image = None
    image_offset = None
    for low, high, name in RANGES:
        if static is not None and low <= static < high:
            image = name
            image_offset = static - low
            break
    return {
        "runtime": runtime,
        "static": static,
        "image": image,
        "image_offset": image_offset,
    }


def _emit(kind, **fields):
    record = {
        "kind": kind,
        "epoch": time.time(),
        "elapsed": (time.time() - TRACE["started"]) if TRACE["started"] else 0.0,
        "hop": TRACE["hops"],
    }
    record.update(fields)
    print("TRACE_JSON " + json.dumps(record, sort_keys=True), flush=True)


def _registers(frame):
    result = {}
    for name in ["pc", "lr", "sp", "tpidr_el1"] + ["x%d" % i for i in range(18)]:
        value = _reg(frame, name)
        result[name] = value
    return result


def _matches(frame):
    return (TRACE["armed"] and not TRACE["stopped"] and
            _reg(frame, "tpidr_el1") == TRACE["candidate_tp"] and
            frame.GetThread().GetThreadID() == TRACE["candidate_thread"])


def _stop(reason, frame, **fields):
    if TRACE["stopped"]:
        return True
    TRACE["stopped"] = True
    fields.update({
        "reason": reason,
        "thread": frame.GetThread().GetThreadID(),
        "tpidr_el1": _reg(frame, "tpidr_el1"),
        "pc": _address_meta(_reg(frame, "pc")),
        "registers": _registers(frame),
    })
    _emit("stop", **fields)
    print("TRACE_STOP reason=%s pc=0x%x hops=%d breakpoints=%d" %
          (reason, _reg(frame, "pc") or 0, TRACE["hops"],
           TRACE["dynamic_breakpoints"]), flush=True)
    return True


def _budget_check(frame):
    if TRACE["hops"] >= MAX_HOPS:
        return _stop("hop-limit", frame, limit=MAX_HOPS)
    if TRACE["dynamic_breakpoints"] >= MAX_BREAKPOINTS:
        return _stop("breakpoint-limit", frame, limit=MAX_BREAKPOINTS)
    if TRACE["started"] and time.time() - TRACE["started"] >= MAX_SECONDS:
        return _stop("trace-time-limit", frame, limit=MAX_SECONDS)
    return False


def _new_breakpoint(target, address, callback, table, metadata):
    before = target.GetNumBreakpoints()
    breakpoint = target.BreakpointCreateByAddress(address)
    if target.GetNumBreakpoints() != before + 1:
        raise RuntimeError("failed dynamic breakpoint at 0x%x" % address)
    breakpoint.SetScriptCallbackFunction(
        "dcp_sm_dynamic_trace_callbacks.%s" % callback)
    table[breakpoint.GetID()] = metadata
    TRACE["dynamic_breakpoints"] += 1
    return breakpoint


def _install_target(process, address, source):
    target = process.GetTarget()
    breakpoint_id = TARGET_BY_ADDRESS.get(address)
    if breakpoint_id is not None:
        return breakpoint_id
    breakpoint = _new_breakpoint(target, address, "on_target_entry", TARGETS,
                                 {"address": address, "sources": [source]})
    TARGET_BY_ADDRESS[address] = breakpoint.GetID()
    print("DYNAMIC_BREAKPOINT_PROOF id=%d label=SM_TRACE_TARGET address=0x%x" %
          (breakpoint.GetID(), address), flush=True)
    return breakpoint.GetID()


def _install_return(process, address):
    target = process.GetTarget()
    breakpoint_id = RETURN_BY_ADDRESS.get(address)
    if breakpoint_id is not None:
        return breakpoint_id
    breakpoint = _new_breakpoint(target, address, "on_dynamic_return", RETURNS,
                                 {"address": address})
    RETURN_BY_ADDRESS[address] = breakpoint.GetID()
    print("DYNAMIC_BREAKPOINT_PROOF id=%d label=SM_TRACE_RETURN address=0x%x" %
          (breakpoint.GetID(), address), flush=True)
    return breakpoint.GetID()


def _target_register(mnemonic, operands):
    if mnemonic not in ("br", "braa", "brab", "braaz", "brabz",
                        "blr", "blraa", "blrab", "blraaz", "blrabz"):
        return None
    match = re.search(r"\b(x(?:[12]?[0-9]|3[01]))\b", operands or "")
    return match.group(1) if match else None


def _infer_slot(history, target_register, static_site):
    if static_site in KNOWN_SLOTS:
        return KNOWN_SLOTS[static_site], None
    wanted = target_register
    for mnemonic, operands in reversed(history[-20:]):
        operands = operands or ""
        move = re.match(r"(x\d+),\s*(x\d+)$", operands)
        if mnemonic == "mov" and move and move.group(1) == wanted:
            wanted = move.group(2)
            continue
        load = re.match(r"(x\d+),\s*\[(x\d+)(?:,\s*#?(0x[0-9a-f]+|\d+))?\]!?(?:.*)$",
                        operands)
        if mnemonic.startswith("ldr") and load and load.group(1) == wanted:
            return int(load.group(3) or "0", 0), load.group(2)
    return None, None


def _scan_target(process, runtime_address):
    if runtime_address in SCANNED:
        return 0
    SCANNED.add(runtime_address)
    target = process.GetTarget()
    instructions = target.ReadInstructions(lldb.SBAddress(runtime_address, target),
                                           SCAN_INSTRUCTIONS)
    history = []
    found = 0
    for index in range(instructions.GetSize()):
        instruction = instructions.GetInstructionAtIndex(index)
        address = instruction.GetAddress().GetLoadAddress(target)
        mnemonic = (instruction.GetMnemonic(target) or "").lower()
        operands = instruction.GetOperands(target) or ""
        if index and mnemonic in ("pacibsp", "paciasp"):
            break
        register = _target_register(mnemonic, operands)
        if register:
            static_site = address - KSLIDE
            slot, base_register = _infer_slot(history, register, static_site)
            metadata = {
                "address": address,
                "caller": runtime_address,
                "mnemonic": mnemonic,
                "operands": operands,
                "target_register": register,
                "tail": not mnemonic.startswith("bl"),
                "return_address": None if not mnemonic.startswith("bl") else address + 4,
                "vtable_slot": slot,
                "vtable_base_register": base_register,
            }
            breakpoint = _new_breakpoint(target, address, "on_indirect_call", SITES,
                                         metadata)
            print("DYNAMIC_BREAKPOINT_PROOF id=%d label=SM_TRACE_INDIRECT "
                  "address=0x%x mnemonic=%s" %
                  (breakpoint.GetID(), address, mnemonic), flush=True)
            found += 1
        history.append((mnemonic, operands))
        if mnemonic in ("ret", "retab"):
            break
    _emit("scan", function=_address_meta(runtime_address), indirect_sites=found,
          instruction_count=instructions.GetSize())
    return found


def _queue_target(frame, target_address, source, has_return):
    process = frame.GetThread().GetProcess()
    target_id = _install_target(process, target_address, source)
    tp = TRACE["candidate_tp"]
    EXPECTED.setdefault(tp, []).append({
        "target": target_address,
        "source": source,
        "target_breakpoint": target_id,
    })
    if has_return:
        return_address = source["return_address"]
        _install_return(process, return_address)
        PENDING_RETURNS.setdefault(tp, []).append({
            "return_address": return_address,
            "target": target_address,
            "source": source,
            "started": time.time(),
        })


def on_afk_state(frame, _bp_loc, _dict):
    x0, x1, x2 = (_reg(frame, name) for name in ("x0", "x1", "x2"))
    if x1 == 0 and x2:
        next_tp = _reg(frame, "tpidr_el1")
        next_thread = frame.GetThread().GetThreadID()
        new_origin = (next_tp != TRACE["candidate_tp"] or
                      next_thread != TRACE["candidate_thread"] or
                      x0 != TRACE["afk_client"])
        TRACE["candidate_tp"] = next_tp
        TRACE["candidate_thread"] = next_thread
        TRACE["afk_client"] = x0
        if new_origin:
            # A completed state task may be followed by another AFK client in
            # the same boot (and, for DCP, after an RTKit restart).  Keep the
            # dynamically discovered breakpoint graph, but discard filters
            # and return state that belong only to the previous origin.
            TRACE["machine"] = None
            TRACE["transition"] = None
            TRACE["gate"] = None
            TRACE["context"] = None
            TRACE["owner"] = None
            TRACE["lock"] = None
            TRACE["wait_event"] = None
            TRACE["stage2_returned"] = False
        print("SM_TRACE_CANDIDATE thread=0x%x tp=0x%x client=0x%x" %
              (TRACE["candidate_thread"], TRACE["candidate_tp"] or 0, x0 or 0),
              flush=True)
    return False


def on_afk_request(frame, _bp_loc, _dict):
    if (_reg(frame, "tpidr_el1") != TRACE["candidate_tp"] or
            _reg(frame, "x0") != TRACE["afk_client"]):
        return False
    state = _reg(frame, "x1")
    offsets = {2: 0x130, 0: 0x138, 1: 0x140}
    offset = offsets.get(state)
    if offset is not None:
        TRACE["requested_state"] = _u64(frame.GetThread().GetProcess(),
                                         TRACE["afk_client"] + offset)
    return False


def on_transition(frame, _bp_loc, _dict):
    if (_reg(frame, "tpidr_el1") != TRACE["candidate_tp"] or
            frame.GetThread().GetThreadID() != TRACE["candidate_thread"]):
        return False
    process = frame.GetThread().GetProcess()
    transition = _reg(frame, "x1")
    destination = _u64(process, transition + 0x20) if transition else None
    client_snapshot = _client_snapshot(process, TRACE["afk_client"])
    _emit("transition-observed", thread=TRACE["candidate_thread"],
          tpidr_el1=TRACE["candidate_tp"], machine=_reg(frame, "x0"),
          transition=_transition_snapshot(process, transition, client_snapshot),
          afk_client=client_snapshot,
          requested_state=TRACE["requested_state"],
          requested_state_name=_state_name(client_snapshot,
                                           TRACE["requested_state"]))
    if not TRACE["armed"] and destination == TRACE["requested_state"]:
        TRACE["armed"] = True
        TRACE["started"] = time.time()
        TRACE["machine"] = _reg(frame, "x0")
        TRACE["transition"] = transition
        _emit("armed", thread=TRACE["candidate_thread"],
              tpidr_el1=TRACE["candidate_tp"], machine=TRACE["machine"],
              transition=transition, afk_client=TRACE["afk_client"],
              destination=destination)
        print("SM_TRACE_ARMED third-transition machine=0x%x transition=0x%x "
              "client=0x%x" % (TRACE["machine"] or 0, transition or 0,
                                TRACE["afk_client"] or 0), flush=True)
    return False


def on_stage2_pre(frame, _bp_loc, _dict):
    current_tp = _reg(frame, "tpidr_el1")
    current_thread = frame.GetThread().GetThreadID()
    operation = _reg(frame, "x0")
    context = _reg(frame, "x1")
    if not _matches(frame) or TRACE["stage2_returned"]:
        # The display-power transaction can enter the same proven stage-2
        # executor without first crossing the AFK state-1 helper used to arm
        # the initial trace.  Rebase at this exact pre-call boundary instead
        # of silently discarding it.  operation+0x20 is the AFK client field
        # established by the earlier instrumented transitions.
        process = frame.GetThread().GetProcess()
        TRACE["candidate_tp"] = current_tp
        TRACE["candidate_thread"] = current_thread
        TRACE["afk_client"] = _u64(process, operation + 0x20) if operation else None
        TRACE["requested_state"] = context
        TRACE["machine"] = None
        TRACE["transition"] = None
        TRACE["gate"] = None
        TRACE["context"] = None
        TRACE["owner"] = None
        TRACE["lock"] = None
        TRACE["wait_event"] = None
        TRACE["stage2_returned"] = False
        TRACE["armed"] = True
        if TRACE["started"] is None:
            TRACE["started"] = time.time()
        client_snapshot = _client_snapshot(process, TRACE["afk_client"])
        _emit("stage2-rebase", thread=current_thread, tpidr_el1=current_tp,
              operation=_operation_snapshot(process, operation),
              context=context,
              context_state=_state_name(client_snapshot, context),
              afk_client=client_snapshot)
    if _budget_check(frame):
        return True
    process = frame.GetThread().GetProcess()
    target_address = _kptr(_reg(frame, "x9"))
    client = _u64(process, operation + 0x20) if operation else None
    client_snapshot = _client_snapshot(process, client)
    target_meta = _address_meta(target_address)
    source = {
        "caller": _address_meta(_reg(frame, "pc")),
        "callsite": _address_meta(_reg(frame, "pc")),
        "mnemonic": "blraa",
        "vtable_slot": 0x10,
        "operation": operation,
        "operation_snapshot": _operation_snapshot(process, operation),
        "context": context,
        "context_state": _state_name(client_snapshot, context),
        "afk_client": client,
        "afk_client_snapshot": client_snapshot,
        "action": ACTION_NAMES.get(target_meta["static"]),
        "return_address": _reg(frame, "pc") + 4,
        "registers": _registers(frame),
    }
    _emit("indirect-call", target=target_meta, **source)
    _queue_target(frame, target_address, source, False)
    return False


def on_indirect_call(frame, bp_loc, _dict):
    if not _matches(frame):
        return False
    if _budget_check(frame):
        return True
    site = SITES[bp_loc.GetBreakpoint().GetID()]
    target_address = _kptr(_reg(frame, site["target_register"]))
    if not target_address:
        return _stop("unresolved-indirect-target", frame, site=site)
    base_value = (_reg(frame, site["vtable_base_register"])
                  if site["vtable_base_register"] else None)
    source = dict(site)
    source.update({
        "caller": _address_meta(site["caller"]),
        "callsite": _address_meta(site["address"]),
        "vtable_base_value": base_value,
        "registers": _registers(frame),
    })
    _emit("indirect-tail" if site["tail"] else "indirect-call",
          target=_address_meta(target_address), **source)
    _queue_target(frame, target_address, source, not site["tail"])
    return False


def on_target_entry(frame, bp_loc, _dict):
    if not _matches(frame):
        return False
    address = TARGETS[bp_loc.GetBreakpoint().GetID()]["address"]
    expected = EXPECTED.get(TRACE["candidate_tp"], [])
    match_index = next((index for index in range(len(expected) - 1, -1, -1)
                        if expected[index]["target"] == address), None)
    if match_index is None:
        return False
    pending = expected.pop(match_index)
    TRACE["hops"] += 1
    registers = _registers(frame)
    _emit("target-entry", target=_address_meta(address),
          source=pending["source"], registers=registers,
          thread=frame.GetThread().GetThreadID(),
          tpidr_el1=TRACE["candidate_tp"])
    # The command-gate +0xf0 target is the recursive-lock sleep wrapper.  Keep
    # the derived owner/lock only as a filter for the concrete primitive.
    if address - KSLIDE == 0xfffffff00b23b410:
        TRACE["owner"] = _reg(frame, "x0")
        TRACE["context"] = _reg(frame, "x1")
        TRACE["lock"] = _u64(frame.GetThread().GetProcess(), TRACE["owner"] + 0x10)
    if _budget_check(frame):
        return True
    _scan_target(frame.GetThread().GetProcess(), address)
    return False


def on_dynamic_return(frame, bp_loc, _dict):
    if not _matches(frame):
        return False
    address = RETURNS[bp_loc.GetBreakpoint().GetID()]["address"]
    pending = PENDING_RETURNS.get(TRACE["candidate_tp"], [])
    match_index = next((index for index in range(len(pending) - 1, -1, -1)
                        if pending[index]["return_address"] == address), None)
    if match_index is None:
        return False
    call = pending.pop(match_index)
    _emit("return", return_site=_address_meta(address),
          target=_address_meta(call["target"]),
          duration=time.time() - call["started"], result=_reg(frame, "x0"),
          source=call["source"])
    return False


def on_stage2_return(frame, _bp_loc, _dict):
    if not _matches(frame):
        return False
    if STOP_ON_STAGE2_RETURN:
        return _stop("stage2-returned", frame, result=_reg(frame, "x0"))
    _emit("stage2-return", result=_reg(frame, "x0"),
          return_site=_address_meta(_reg(frame, "pc")),
          thread=frame.GetThread().GetThreadID(),
          tpidr_el1=TRACE["candidate_tp"], registers=_registers(frame))
    TRACE["stage2_returned"] = True
    # The first wait belongs to the completed command-gate action.  Do not use
    # its lock/context as a filter for the next downstream wait.
    TRACE["owner"] = None
    TRACE["context"] = None
    TRACE["lock"] = None
    TRACE["wait_event"] = None
    if _budget_check(frame):
        return True
    _scan_target(frame.GetThread().GetProcess(), _reg(frame, "pc"))
    return False


def on_wait_primitive(frame, _bp_loc, _dict):
    if not _matches(frame):
        return False
    x0, x1, x2, x3 = (_reg(frame, name) for name in ("x0", "x1", "x2", "x3"))
    if TRACE["lock"] and (x0 != TRACE["lock"] or x2 != TRACE["context"]):
        return False
    TRACE["wait_event"] = x2
    process = frame.GetThread().GetProcess()
    fields = {
        "primitive": _address_meta(_reg(frame, "pc")),
        "lock": x0,
        "flags": x1,
        "wait_event": x2,
        "interruptibility": x3,
        "owner": TRACE["owner"],
        "lock_owner": _u64(process, x0 + 0x18) if x0 else None,
        "lock_recursion": _u64(process, x0 + 0x20) if x0 else None,
        "producer_candidate": _address_meta(0xfffffff008b81f44 + KSLIDE),
    }
    # A caller that explicitly keeps both boundaries transparent wants the
    # same-boot trace to continue beyond stage-2 and through later waits.  The
    # old ``or stage2_returned`` clause silently overrode STOP_ON_WAIT=0 and
    # froze the first post-return sleep, defeating that mode.
    if STOP_ON_WAIT:
        return _stop("concrete-sleep-primitive", frame, **fields)
    _emit("wait-entry", thread=frame.GetThread().GetThreadID(),
          tpidr_el1=TRACE["candidate_tp"], registers=_registers(frame), **fields)
    return False


def on_completion(frame, _bp_loc, _dict):
    if not TRACE["armed"]:
        return False
    x0, x1 = _reg(frame, "x0"), _reg(frame, "x1")
    if x0 != TRACE["afk_client"]:
        return False
    _emit("completion-callback", target=_address_meta(_reg(frame, "pc")),
          thread=frame.GetThread().GetThreadID(),
          tpidr_el1=_reg(frame, "tpidr_el1"), client=x0, wake_event=x1,
          same_origin=_matches(frame), registers=_registers(frame))
    return False


def on_state_tx(frame, _bp_loc, _dict):
    """Witness StateTask's four-byte AP-to-firmware state transmission."""
    if not TRACE["afk_client"]:
        return False
    task = _reg(frame, "x0")
    if task != TRACE["afk_client"] + 0x88:
        return False
    _emit("state-tx", target=_address_meta(_reg(frame, "pc")),
          thread=frame.GetThread().GetThreadID(),
          tpidr_el1=_reg(frame, "tpidr_el1"), task=task,
          value=_reg(frame, "x1") & 0xffffffff,
          same_origin=_matches(frame),
          task_snapshot=_task_snapshot(frame.GetThread().GetProcess(), task),
          registers=_registers(frame))
    return False


def on_state_rx(frame, _bp_loc, _dict):
    """Witness a firmware-to-AP state frame before StateTask parses it."""
    if not TRACE["afk_client"]:
        return False
    task = _reg(frame, "x0")
    if task != TRACE["afk_client"] + 0x88:
        return False
    process = frame.GetThread().GetProcess()
    data, length = _reg(frame, "x1"), _reg(frame, "x2")
    bounded_length = min(length or 0, 0x40)
    raw = _read(process, data, bounded_length)
    _emit("state-rx", target=_address_meta(_reg(frame, "pc")),
          thread=frame.GetThread().GetThreadID(),
          tpidr_el1=_reg(frame, "tpidr_el1"), task=task,
          data=data, length=length, raw=raw.hex() if raw else None,
          same_origin=_matches(frame), task_snapshot=_task_snapshot(process, task),
          registers=_registers(frame))
    return False


def on_state_apply(frame, _bp_loc, _dict):
    """Witness the callback that copies a received state into StateTask+0x49."""
    if not TRACE["afk_client"]:
        return False
    process = frame.GetThread().GetProcess()
    update = _reg(frame, "x0")
    task = _u64(process, update) if update else None
    if task != TRACE["afk_client"] + 0x88:
        return False
    raw = _read(process, update + 8, 8)
    _emit("state-apply", target=_address_meta(_reg(frame, "pc")),
          thread=frame.GetThread().GetThreadID(),
          tpidr_el1=_reg(frame, "tpidr_el1"), task=task, update=update,
          value=raw[0] if raw else None, raw=raw.hex() if raw else None,
          task_snapshot=_task_snapshot(process, task),
          registers=_registers(frame))
    return False


def on_task_mode(frame, _bp_loc, _dict):
    """Witness the virtual mode update that reinstalls StateTask's type-2 goal."""
    TRACE["task_mode_hits"] += 1
    if TRACE["task_mode_hits"] > 64:
        return False
    task = _reg(frame, "x0")
    client = task - 0x88 if task else None
    thread = frame.GetThread()
    try:
        backtrace = [_address_meta(thread.GetFrameAtIndex(index).GetPC() &
                                   0x0000ffffffffffff)
                     for index in range(min(thread.GetNumFrames(), 12))]
    except Exception:
        backtrace = []
    _emit("task-mode", target=_address_meta(_reg(frame, "pc")),
          thread=thread.GetThreadID(), tpidr_el1=_reg(frame, "tpidr_el1"),
          task=task, afk_client=client,
          tracked_client=(client == TRACE["afk_client"]),
          mode=_reg(frame, "x1") & 0xffffffff,
          task_snapshot=_task_snapshot(thread.GetProcess(), task),
          backtrace=backtrace, registers=_registers(frame))
    return False


def _install_static(target, interpreter, static, label, callback):
    runtime = static + KSLIDE
    before = target.GetNumBreakpoints()
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand("breakpoint set --address 0x%x" % runtime, result)
    if target.GetNumBreakpoints() != before + 1:
        raise RuntimeError("failed %s at 0x%x: %s" %
                           (label, runtime, result.GetError()))
    breakpoint = target.GetBreakpointAtIndex(before)
    CONFIG[breakpoint.GetID()] = {"label": label, "address": runtime}
    result = lldb.SBCommandReturnObject()
    interpreter.HandleCommand(
        "breakpoint command add -F dcp_sm_dynamic_trace_callbacks.%s %d" %
        (callback, breakpoint.GetID()), result)
    if not result.Succeeded():
        raise RuntimeError("callback attach failed for %s: %s" %
                           (label, result.GetError()))
    print("COMMAND_LIST_PROOF id=%d label=%s address=0x%x" %
          (breakpoint.GetID(), label, runtime), flush=True)


def install(debugger, _slide):
    result = lldb.SBCommandReturnObject()
    debugger.GetCommandInterpreter().HandleCommand(
        "command script import %s" %
        os.path.join(os.path.dirname(__file__), "display_iokit_callbacks.py"), result)
    if not result.Succeeded():
        raise RuntimeError("could not register display_iokit_callbacks: %s" %
                           result.GetError())
    display_iokit_callbacks.install(debugger, _slide)
    target = debugger.GetSelectedTarget()
    interpreter = debugger.GetCommandInterpreter()
    for static, label, callback in (
        (0xfffffff008ba47b0, "SM_TRACE_AFk_STATE", "on_afk_state"),
        (0xfffffff008b82378, "SM_TRACE_AFk_REQUEST", "on_afk_request"),
        (0xfffffff008b8c2d0, "SM_TRACE_TRANSITION", "on_transition"),
        (0xfffffff008b8c4e8, "SM_TRACE_STAGE2_PRE", "on_stage2_pre"),
        (0xfffffff008b8c4ec, "SM_TRACE_STAGE2_RETURN", "on_stage2_return"),
        # Live ROOT_GATE_ACTION1 proves this is entered from the resolved
        # +0xf0 target with (lock, 0x10, context, 2).
        (0xfffffff00ab002a8, "SM_TRACE_SLEEP", "on_wait_primitive"),
        # Registered by the stage-2 task as its completion/wakeup callback.
        (0xfffffff008b81f44, "SM_TRACE_COMPLETION", "on_completion"),
        # StateTask's proven four-byte transmit builder and receive parser.
        (0xfffffff008b918d8, "SM_TRACE_STATE_TX", "on_state_tx"),
        (0xfffffff008b919f8, "SM_TRACE_STATE_RX", "on_state_rx"),
        # Virtual receive action that writes the decoded state to task+0x49.
        (0xfffffff008b92bbc, "SM_TRACE_STATE_APPLY", "on_state_apply"),
        # Virtual lifecycle/mode setter; it reinstalls the type-2 state goal.
        (0xfffffff008b90ea0, "SM_TRACE_TASK_MODE", "on_task_mode"),
    ):
        _install_static(target, interpreter, static, label, callback)
