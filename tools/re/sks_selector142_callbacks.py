"""Trace public AppleSEPKeyStore selector 142 in a live normal boot.

Selector 142 is switch index 141 in AppleSEPKeyStoreUserClient's external
method dispatcher.  Its case starts at static 0xfffffff0095530a0, accepts one
input scalar, and calls the selector-specific helper at 0xfffffff00954fca4.
The fixed return site is 0xfffffff00955311c.  This read-only callback records
the process, scalar, thread, and helper result without breaking on the much
higher-volume wire-op19 traffic.
"""

import json
import os
import time

import lldb


KERNEL_SLIDE = int(os.environ.get("DVM_KERNEL_SLIDE", "0x20000000"), 0)
SELECTOR142_ENTRY = 0xfffffff0095530a0 + KERNEL_SLIDE
SELECTOR142_RETURN = 0xfffffff00955311c + KERNEL_SLIDE
PROGNAME_PTR = 0x1e6ef1590
SLIDE = [0]
CALLS = [0]
PENDING = {}
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
    return (data.split(b"\0", 1)[0].decode("utf-8", "replace")
            if data else "<unreadable>")


def _progname(process):
    indirect = _u64(process, SLIDE[0] + PROGNAME_PTR)
    string = _u64(process, indirect) if indirect else None
    return _cstring(process, string) if string else "<progname-unreadable>"


def _write_event(name, payload):
    if not EVENT_DIR:
        return
    os.makedirs(EVENT_DIR, exist_ok=True)
    path = os.path.join(EVENT_DIR, name)
    temporary = "%s.%d.tmp" % (path, os.getpid())
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def on_entry(frame, _bp_loc, _internal_dict):
    process = frame.GetThread().GetProcess()
    arguments = _reg(frame, "x19") or 0
    scalar_pointer = _u64(process, arguments + 0x20)
    CALLS[0] += 1
    payload = {
        "kind": "entry",
        "ordinal": CALLS[0],
        "epoch": time.time(),
        "progname": _progname(process),
        "thread": frame.GetThread().GetThreadID(),
        "tpidr_el1": _reg(frame, "tpidr_el1"),
        "arguments": arguments,
        "scalar_pointer": scalar_pointer,
        "scalar": _u32(process, scalar_pointer) if scalar_pointer else None,
        "user_client": _reg(frame, "x22"),
    }
    PENDING.setdefault(payload["tpidr_el1"], []).append(payload)
    print("SKS_SELECTOR142 " + json.dumps(payload, sort_keys=True), flush=True)
    _write_event("selector142-last-entry.json", payload)
    return False


def on_return(frame, _bp_loc, _internal_dict):
    thread_pointer = _reg(frame, "tpidr_el1")
    pending = PENDING.get(thread_pointer)
    if not pending:
        return False
    entry = pending.pop()
    if not pending:
        PENDING.pop(thread_pointer, None)
    result = _reg(frame, "w0")
    if result is not None and result & 0x80000000:
        result -= 1 << 32
    payload = dict(entry)
    payload.update({
        "kind": "return",
        "result": result,
        "return_epoch": time.time(),
    })
    print("SKS_SELECTOR142 " + json.dumps(payload, sort_keys=True), flush=True)
    _write_event("selector142-last-return.json", payload)
    return False


def _install(target, address, callback, label):
    breakpoint = target.BreakpointCreateByAddress(address)
    breakpoint.SetScriptCallbackFunction(
        "sks_selector142_callbacks." + callback)
    breakpoint.SetAutoContinue(True)
    print("COMMAND_LIST_PROOF id=%d label=%s address=0x%x locations=%d" %
          (breakpoint.GetID(), label, address,
           breakpoint.GetNumLocations()), flush=True)
    if breakpoint.GetNumLocations() != 1:
        raise RuntimeError("%s expected one location, got %d" %
                           (label, breakpoint.GetNumLocations()))


def install(debugger, slide):
    SLIDE[0] = int(slide)
    target = debugger.GetSelectedTarget()
    _install(target, SELECTOR142_ENTRY, "on_entry", "SKS_SELECTOR142_ENTRY")
    _install(target, SELECTOR142_RETURN, "on_return", "SKS_SELECTOR142_RETURN")
    _write_event("selector142-ready.json", {
        "epoch": time.time(),
        "slide": SLIDE[0],
        "entry": SELECTOR142_ENTRY,
        "return": SELECTOR142_RETURN,
    })
