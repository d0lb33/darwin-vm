"""Bounded LLDB witness for AppleSEPKeyStore public selector 7.

The selector-7 switch arm is AppleSEPKeyStore static 0xfffffff009555038.
It receives IOExternalMethodArguments in x19, calls wire operation 0x19, and
reaches 0xfffffff009559d8c only after the authenticated reply and DER record
have decoded successfully.  Capture three entry/result pairs, name the caller
through libsystem_c's per-process ``___progname_pointer``, then request an
early probe stop.  This callback never changes guest state.
"""

import json
import os
import time

import lldb


KERNEL_SLIDE = 0x20000000
SELECTOR7_CASE = 0xfffffff009555038 + KERNEL_SLIDE
SELECTOR7_RESULT = 0xfffffff009559d8c + KERNEL_SLIDE
PROGNAME_PTR = 0x1e6ef1590
SLIDE = [0]
ENTRIES = {}
COUNTS = {"entry": 0, "result": 0}
EVENT_DIR = os.environ.get("DVM_PROBE_EVENT_DIR", "")


def _reg(frame, name):
    value = frame.FindRegister(name)
    return value.GetValueAsUnsigned() if value.IsValid() else None


def _read(process, address, size):
    if not address:
        return b""
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    return data if error.Success() else b""


def _u32(process, address):
    data = _read(process, address, 4)
    return int.from_bytes(data, "little") if len(data) == 4 else None


def _u64(process, address):
    data = _read(process, address, 8)
    return int.from_bytes(data, "little") if len(data) == 8 else None


def _cstring(process, address, limit=96):
    data = _read(process, address, limit)
    if not data:
        return "<unreadable>"
    return data.split(b"\0", 1)[0].decode("utf-8", "replace")


def _progname(process):
    indirect = _u64(process, SLIDE[0] + PROGNAME_PTR)
    string = _u64(process, indirect) if indirect else None
    return _cstring(process, string) if string else "<progname-unreadable>"


def _write_success(payload):
    if not EVENT_DIR:
        return
    os.makedirs(EVENT_DIR, exist_ok=True)
    path = os.path.join(EVENT_DIR, "success.SKS_SELECTOR7_THREE_RESULTS.json")
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _backtrace(thread):
    pcs = []
    for index in range(min(thread.GetNumFrames(), 18)):
        pc = thread.GetFrameAtIndex(index).GetPC() & 0x0000ffffffffffff
        if 0x180000000 + SLIDE[0] <= pc < 0x340000000 + SLIDE[0]:
            pcs.append("0x%x" % (pc - SLIDE[0]))
        elif pc >= KERNEL_SLIDE:
            pcs.append("0x%x" % (pc - KERNEL_SLIDE))
        else:
            pcs.append("0x%x" % pc)
    return pcs


def on_entry(frame, bp_loc, _internal_dict):
    if COUNTS["entry"] >= 3:
        return False
    thread = frame.GetThread()
    process = thread.GetProcess()
    tp = _reg(frame, "tpidr_el1") or 0
    args = _reg(frame, "x19") or 0
    scalar_input = _u64(process, args + 0x20) if args else None
    scalar_input_count = _u32(process, args + 0x28) if args else None
    scalar_output = _u64(process, args + 0x48) if args else None
    scalar_output_count = _u32(process, args + 0x50) if args else None
    requested_state = _u64(process, scalar_input) if scalar_input else None
    if requested_state is not None and requested_state & (1 << 63):
        requested_state -= 1 << 64
    COUNTS["entry"] += 1
    entry = {
        "ordinal": COUNTS["entry"],
        "epoch": time.time(),
        "thread": thread.GetThreadID(),
        "tpidr_el1": tp,
        "progname": _progname(process),
        "args": args,
        "scalar_input": scalar_input,
        "scalar_input_count": scalar_input_count,
        "scalar_output": scalar_output,
        "scalar_output_count": scalar_output_count,
        "requested_state": requested_state,
        "backtrace": _backtrace(thread),
    }
    ENTRIES[tp] = entry
    print("SKS_SELECTOR7_ENTRY " + json.dumps(entry, sort_keys=True), flush=True)
    if COUNTS["entry"] == 3:
        bp_loc.GetBreakpoint().SetEnabled(False)
    return False


def on_result(frame, bp_loc, _internal_dict):
    if COUNTS["result"] >= 3:
        return False
    thread = frame.GetThread()
    process = thread.GetProcess()
    tp = _reg(frame, "tpidr_el1") or 0
    entry = ENTRIES.pop(tp, None)
    if entry is None:
        return False
    sp = _reg(frame, "sp") or 0
    decoded_output = _u32(process, sp + 0xd0) if sp else None
    response_length = _u32(process, sp + 0xc0) if sp else None
    response_pointer = _u64(process, (_reg(frame, "x21") or 0) + 0xa8)
    response = _read(process, response_pointer, min(response_length or 0, 0x160))
    COUNTS["result"] += 1
    result = dict(entry)
    result.update({
        "result_ordinal": COUNTS["result"],
        "result_epoch": time.time(),
        "decoded_output": decoded_output,
        "response_length": response_length,
        "response_pointer": response_pointer,
        "response_prefix": response[:64].hex(),
    })
    print("SKS_SELECTOR7_RESULT " + json.dumps(result, sort_keys=True), flush=True)
    if COUNTS["result"] == 3:
        bp_loc.GetBreakpoint().SetEnabled(False)
        _write_success(result)
    return False


def _install(target, address, callback, label):
    bp = target.BreakpointCreateByAddress(address)
    bp.SetScriptCallbackFunction("sks_selector7_callbacks." + callback)
    print(
        "COMMAND_LIST_PROOF id=%d label=%s address=0x%x locations=%d"
        % (bp.GetID(), label, address, bp.GetNumLocations()),
        flush=True,
    )
    if bp.GetNumLocations() != 1:
        raise RuntimeError("%s expected one location, got %d" %
                           (label, bp.GetNumLocations()))


def install(debugger, slide):
    SLIDE[0] = slide
    target = debugger.GetSelectedTarget()
    _install(target, SELECTOR7_CASE, "on_entry", "SKS_SELECTOR7_ENTRY")
    _install(target, SELECTOR7_RESULT, "on_result", "SKS_SELECTOR7_RESULT")
