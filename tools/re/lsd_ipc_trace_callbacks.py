"""Trace LaunchServices mach_msg2 traffic in an already-running guest.

This module is intentionally usable from an interactive LLDB attachment so a
frozen setup-gate boot can be extended without rebooting.  It filters the
shared-cache trap by ``__progname == "lsd"``, records the packed mach_msg2 ABI,
and plants a same-thread return witness at each observed caller LR.  Receive
buffers are read again on return, after the kernel has translated port names.

Load with::

    script import sys; sys.path.insert(0, "tools/re")
    script import lsd_ipc_trace_callbacks as lit
    script lit.install(lldb.debugger, 0xecb4000)

The static addresses are for iOS 27 beta 8.  ``slide`` must come from the
current boot's shared-cache positive control, never from an earlier boot.
"""
import json
import time

import lldb


MACH_MSG2_TRAP = 0x237CCFCCC
PROGNAME_PTR = 0x1E6EF1590
SLIDE = 0
THREAD_GROUP = 0
CALLS = 0
ACTIVE = {}
RETURN_BPS = {}
SITE_BPS = {}


def _reg(frame, name):
    value = frame.FindRegister(name)
    return value.GetValueAsUnsigned() if value.IsValid() else 0


def _read(process, address, size):
    if not address or not size:
        return b""
    error = lldb.SBError()
    data = process.ReadMemory(address, min(size, 0x400), error)
    return data if error.Success() else b""


def _u64(process, address):
    data = _read(process, address, 8)
    return int.from_bytes(data, "little") if len(data) == 8 else 0


def _cstring(process, address, limit=64):
    data = _read(process, address, limit)
    return data.split(b"\0", 1)[0].decode("ascii", "replace") if data else ""


def _progname(process):
    pointer = _u64(process, SLIDE + PROGNAME_PTR)
    string = _u64(process, pointer) if pointer else 0
    return _cstring(process, string)


def _is_lsd(frame):
    process = frame.GetThread().GetProcess()
    if THREAD_GROUP:
        # thread_group is a stable, process-specific kernel object at +0x290
        # in this boot's struct thread.  This is one remote read instead of
        # three shared-cache reads on every system-wide mach_msg2 hit.
        return _u64(process, _reg(frame, "tpidr_el1") + 0x290) == THREAD_GROUP
    return _progname(process) == "lsd"


def _canonical(value):
    value &= 0x0000FFFFFFFFFFFF
    return value | 0xFFFF000000000000 if value & (1 << 47) else value


def _message(process, address, size):
    data = _read(process, address, size)
    result = {"address": address, "requested": size, "read": len(data),
              "hex": data.hex()}
    if len(data) >= 24:
        result.update({
            "bits": int.from_bytes(data[0:4], "little"),
            "size": int.from_bytes(data[4:8], "little"),
            "remote_port": int.from_bytes(data[8:12], "little"),
            "local_port": int.from_bytes(data[12:16], "little"),
            "voucher_port": int.from_bytes(data[16:20], "little"),
            "id": int.from_bytes(data[20:24], "little"),
        })
    return result


def _arguments(process, frame):
    registers = [_reg(frame, "x%d" % index) for index in range(8)]
    options = registers[1]
    result = {
        "registers": {"x%d" % index: value
                      for index, value in enumerate(registers)},
        "options": options,
        "vector": bool(options & (1 << 32)),
        "send_count": registers[2] >> 32,
        "receive_count": registers[6] & 0xFFFFFFFF,
        "remote_port": registers[3] & 0xFFFFFFFF,
        "local_port": registers[3] >> 32,
        "voucher_port": registers[4] & 0xFFFFFFFF,
        "message_id": registers[4] >> 32,
        "descriptor_count": registers[5] & 0xFFFFFFFF,
        "receive_name": registers[5] >> 32,
        "timeout": registers[7],
    }
    if not result["vector"]:
        result["send_address"] = registers[0]
        result["receive_address"] = registers[0]
        result["entry_message"] = _message(
            process, registers[0], result["send_count"])
        return result

    raw = _read(process, registers[0], 48)
    result["vectors_raw"] = raw.hex()
    vectors = []
    for offset in range(0, len(raw) - 23, 24):
        vectors.append({
            "data": int.from_bytes(raw[offset:offset + 8], "little"),
            "receive_address": int.from_bytes(
                raw[offset + 8:offset + 16], "little"),
            "send_size": int.from_bytes(raw[offset + 16:offset + 20], "little"),
            "receive_size": int.from_bytes(raw[offset + 20:offset + 24], "little"),
        })
    result["vectors"] = vectors
    first = vectors[0] if vectors else {}
    result["send_address"] = first.get("data", 0)
    result["receive_address"] = first.get("receive_address", 0)
    result["entry_message"] = _message(
        process, result["send_address"], first.get("send_size", 0))
    return result


def _emit(kind, **fields):
    record = {"kind": kind, "time": time.time()}
    record.update(fields)
    print("LSD_IPC_JSON " + json.dumps(record, sort_keys=True), flush=True)


def _install_return(target, address):
    if address in RETURN_BPS:
        return
    breakpoint = target.BreakpointCreateByAddress(address)
    if not breakpoint.IsValid() or breakpoint.GetNumLocations() != 1:
        raise RuntimeError("failed lsd return breakpoint at 0x%x" % address)
    breakpoint.SetScriptCallbackFunction("lsd_ipc_trace_callbacks.on_return")
    breakpoint.SetAutoContinue(True)
    RETURN_BPS[address] = breakpoint.GetID()
    print("COMMAND_LIST_PROOF id=%d label=LSD_MACH_MSG2_RETURN address=0x%x" %
          (breakpoint.GetID(), address), flush=True)


def on_entry(frame, _bp_loc, _dict):
    global CALLS
    process = frame.GetThread().GetProcess()
    if not _is_lsd(frame):
        return False
    CALLS += 1
    thread_pointer = _reg(frame, "tpidr_el1")
    return_address = _canonical(_reg(frame, "lr"))
    arguments = _arguments(process, frame)
    key = (thread_pointer, return_address)
    ACTIVE.setdefault(key, []).append({
        "call": CALLS,
        "arguments": arguments,
        "started": time.time(),
    })
    _install_return(process.GetTarget(), return_address)
    _emit("entry", call=CALLS, thread_pointer=thread_pointer,
          return_address=return_address, **arguments)
    return False


def on_return(frame, _bp_loc, _dict):
    process = frame.GetThread().GetProcess()
    if not _is_lsd(frame):
        return False
    thread_pointer = _reg(frame, "tpidr_el1")
    return_address = _canonical(_reg(frame, "pc"))
    pending = ACTIVE.get((thread_pointer, return_address))
    if not pending:
        return False
    call = pending.pop()
    if not pending:
        ACTIVE.pop((thread_pointer, return_address), None)
    arguments = call["arguments"]
    receive_address = arguments.get("receive_address", 0)
    receive_size = 0x400
    vectors = arguments.get("vectors", [])
    if vectors:
        receive_size = vectors[0].get("receive_size", receive_size) or receive_size
    _emit("return", call=call["call"], thread_pointer=thread_pointer,
          return_address=return_address, result=_reg(frame, "x0"),
          elapsed=time.time() - call["started"],
          receive_name=arguments.get("receive_name"),
          receive_message=_message(process, receive_address, receive_size))
    return False


def on_site(frame, bp_loc, _dict):
    if not _is_lsd(frame):
        return False
    thread = frame.GetThread()
    frames = []
    for index in range(min(thread.GetNumFrames(), 32)):
        frames.append(_canonical(thread.GetFrameAtIndex(index).GetPC()))
    _emit("site", label=SITE_BPS.get(bp_loc.GetBreakpoint().GetID(), "site"),
          thread_pointer=_reg(frame, "tpidr_el1"), pc=_reg(frame, "pc"),
          lr=_canonical(_reg(frame, "lr")), sp=_reg(frame, "sp"),
          backtrace=frames)
    return False


def install_site(debugger, address, label):
    target = debugger.GetSelectedTarget()
    breakpoint = target.BreakpointCreateByAddress(int(address))
    if not breakpoint.IsValid() or breakpoint.GetNumLocations() != 1:
        raise RuntimeError("failed lsd site breakpoint at 0x%x" % address)
    breakpoint.SetScriptCallbackFunction("lsd_ipc_trace_callbacks.on_site")
    breakpoint.SetAutoContinue(True)
    SITE_BPS[breakpoint.GetID()] = str(label)
    print("COMMAND_LIST_PROOF id=%d label=%s address=0x%x" %
          (breakpoint.GetID(), label, address), flush=True)
    return breakpoint.GetID()


def install(debugger, slide, thread_group=0):
    global SLIDE, THREAD_GROUP
    SLIDE = int(slide)
    THREAD_GROUP = int(thread_group)
    target = debugger.GetSelectedTarget()
    address = SLIDE + MACH_MSG2_TRAP
    breakpoint = target.BreakpointCreateByAddress(address)
    if not breakpoint.IsValid() or breakpoint.GetNumLocations() != 1:
        raise RuntimeError("failed lsd mach_msg2 breakpoint at 0x%x" % address)
    breakpoint.SetScriptCallbackFunction("lsd_ipc_trace_callbacks.on_entry")
    breakpoint.SetAutoContinue(True)
    print("COMMAND_LIST_PROOF id=%d label=LSD_MACH_MSG2_ENTRY address=0x%x slide=0x%x thread_group=0x%x" %
          (breakpoint.GetID(), address, SLIDE, THREAD_GROUP), flush=True)
    return breakpoint.GetID()
