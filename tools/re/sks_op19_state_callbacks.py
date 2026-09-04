"""Bounded LLDB witness for AppleSEPKeyStore wire-op19 state -501.

Public selectors 7, 17, and 35 converge on the AppleSEPKeyStore wrapper at
static 0xfffffff009547360.  At entry x1 is the opaque request context, w2 is
the derived wire-state value, and w3 distinguishes the shared selector-17/35
path.  Log and stop after the first three -501 requests, naming their process
and public-selector path.  This callback is read-only.
"""

import json
import os
import time

import lldb


KERNEL_SLIDE = 0x20000000
OP19_WRAPPER = 0xfffffff009547360 + KERNEL_SLIDE
SELECTOR7_RETURN = 0xfffffff0095550b4
SELECTOR17_35_RETURN = 0xfffffff009556e3c
PROGNAME_PTR = 0x1e6ef1590
SLIDE = [0]
COUNT = [0]
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


def _public_selector(static_lr, mode):
    if static_lr == SELECTOR7_RETURN:
        return 7
    if static_lr == SELECTOR17_35_RETURN:
        return 35 if mode == 0 else 17
    return None


def _write_success(payload):
    if not EVENT_DIR:
        return
    os.makedirs(EVENT_DIR, exist_ok=True)
    path = os.path.join(EVENT_DIR, "success.SKS_OP19_NEG501_THREE.json")
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def on_entry(frame, bp_loc, _internal_dict):
    state = _reg(frame, "w2")
    if state != ((-501) & 0xffffffff):
        return False

    thread = frame.GetThread()
    process = thread.GetProcess()
    lr = (_reg(frame, "lr") or 0) & 0x0000ffffffffffff
    static_lr = lr - KERNEL_SLIDE if lr >= KERNEL_SLIDE else lr
    if static_lr & (1 << 47):
        static_lr |= 0xffff000000000000
    mode = _reg(frame, "w3")
    COUNT[0] += 1
    payload = {
        "ordinal": COUNT[0],
        "epoch": time.time(),
        "thread": thread.GetThreadID(),
        "tpidr_el1": _reg(frame, "tpidr_el1"),
        "progname": _progname(process),
        "wire_state": -501,
        "opaque_context": _reg(frame, "x1"),
        "mode": mode,
        "caller_runtime": lr,
        "caller_static": static_lr,
        "public_selector": _public_selector(static_lr, mode),
    }
    print("SKS_OP19_NEG501 " + json.dumps(payload, sort_keys=True), flush=True)
    if COUNT[0] == 3:
        bp_loc.GetBreakpoint().SetEnabled(False)
        _write_success(payload)
    return False


def install(debugger, slide):
    SLIDE[0] = slide
    target = debugger.GetSelectedTarget()
    bp = target.BreakpointCreateByAddress(OP19_WRAPPER)
    bp.SetScriptCallbackFunction("sks_op19_state_callbacks.on_entry")
    print(
        "COMMAND_LIST_PROOF id=%d label=SKS_OP19_NEG501_ENTRY "
        "address=0x%x locations=%d"
        % (bp.GetID(), OP19_WRAPPER, bp.GetNumLocations()),
        flush=True,
    )
    if bp.GetNumLocations() != 1:
        raise RuntimeError(
            "SKS_OP19_NEG501_ENTRY expected one location, got %d"
            % bp.GetNumLocations()
        )
