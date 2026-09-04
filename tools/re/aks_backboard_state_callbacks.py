"""Trace backboardd's AppleKeyStore device-state calls through return.

Break at the two public AppleKeyStore wrappers used for selectors 17 and 35,
filter to backboardd, and dynamically plant a return breakpoint at each live
caller's LR.  The return witness records the actual decoded 0x42-byte state
structure that the daemon receives.  Stop after three complete calls.  Guest
registers and memory are read only.
"""

import json
import os
import time

import lldb


CACHE_LO = 0x180000000
CACHE_HI = 0x340000000
AKS_GET_EXTENDED_DEVICE_STATE = 0x23e341598
AKS_GET_DEVICE_STATE = 0x23e341654
PROGNAME_PTR = 0x1e6ef1590
SLIDE = [0]
PENDING = {}
RESULTS = [0]
BKD_BASE = [None]
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


def _static_cache(address):
    if CACHE_LO + SLIDE[0] <= address < CACHE_HI + SLIDE[0]:
        return address - SLIDE[0]
    return None


def _find_macho_base(process, address, span=0x800000):
    page = address & ~0x3fff
    low = max(page - span, 0x100000000)
    while page >= low:
        if (_u32(process, page) == 0xfeedfacf and
                _u32(process, page + 4) == 0x0100000c and
                _u32(process, page + 12) == 2):
            return page
        page -= 0x4000
    return None


def _backtrace(thread):
    process = thread.GetProcess()
    result = []
    for index in range(min(thread.GetNumFrames(), 18)):
        pc = thread.GetFrameAtIndex(index).GetPC() & 0x0000ffffffffffff
        static = _static_cache(pc)
        if static is not None:
            result.append({"image": "dyld-cache", "runtime": pc,
                           "static": static})
            continue
        if BKD_BASE[0] is None and 0x100000000 <= pc < 0x140000000:
            BKD_BASE[0] = _find_macho_base(process, pc)
        if BKD_BASE[0] is not None and BKD_BASE[0] <= pc < BKD_BASE[0] + 0x100000:
            result.append({"image": "backboardd", "runtime": pc,
                           "static": 0x100000000 + pc - BKD_BASE[0]})
        else:
            result.append({"image": "unknown", "runtime": pc})
    return result


def _decode_state(data):
    def u32(offset):
        return int.from_bytes(data[offset:offset + 4], "little") \
            if len(data) >= offset + 4 else None

    def u64(offset):
        return int.from_bytes(data[offset:offset + 8], "little") \
            if len(data) >= offset + 8 else None

    return {
        "state": u32(0x00),
        "lock_state": u32(0x04),
        "backoff": u64(0x08),
        "failed_attempts": u32(0x10),
        "generation_state": u32(0x14),
        "assertion_set": u64(0x1a),
        "grace_or_recovery": u64(0x22),
        "more_state": u32(0x2a),
        "keybag_handle": u32(0x2e),
    }


def _write_success(payload):
    if not EVENT_DIR:
        return
    os.makedirs(EVENT_DIR, exist_ok=True)
    path = os.path.join(EVENT_DIR, "success.AKS_BKD_STATE_THREE.json")
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def on_entry(frame, bp_loc, _internal_dict):
    thread = frame.GetThread()
    process = thread.GetProcess()
    if _progname(process) != "backboardd" or RESULTS[0] >= 3:
        return False

    tp = _reg(frame, "tpidr_el1") or 0
    return_address = (_reg(frame, "lr") or 0) & 0x0000ffffffffffff
    entry = {
        "epoch": time.time(),
        "thread": thread.GetThreadID(),
        "tpidr_el1": tp,
        "api": ("aks_get_extended_device_state" if
                frame.GetPC() == AKS_GET_EXTENDED_DEVICE_STATE + SLIDE[0]
                else "aks_get_device_state"),
        "input": _reg(frame, "x0"),
        "output_pointer": _reg(frame, "x1"),
        "return_runtime": return_address,
        "backtrace": _backtrace(thread),
        "backboardd_base": BKD_BASE[0],
    }
    target = process.GetTarget()
    ret = target.BreakpointCreateByAddress(return_address)
    ret.SetScriptCallbackFunction("aks_backboard_state_callbacks.on_return")
    PENDING[ret.GetID()] = entry
    print("AKS_BKD_STATE_ENTRY " + json.dumps(entry, sort_keys=True), flush=True)
    print("DYNAMIC_RETURN_PROOF id=%d address=0x%x locations=%d" %
          (ret.GetID(), return_address, ret.GetNumLocations()), flush=True)
    return False


def on_return(frame, bp_loc, _internal_dict):
    bp = bp_loc.GetBreakpoint()
    entry = PENDING.get(bp.GetID())
    if entry is None or (_reg(frame, "tpidr_el1") or 0) != entry["tpidr_el1"]:
        return False

    process = frame.GetThread().GetProcess()
    # __get_device_state copies exactly 0x42 bytes to the caller at
    # AppleKeyStore 0x23e3414d4..0x23e3414e0.  Bytes beyond that belong to
    # the caller's stack and must not be mistaken for decoded fields.
    data = _read(process, entry["output_pointer"], 0x42)
    status = _reg(frame, "w0")
    if status is not None and status & 0x80000000:
        status -= 1 << 32
    RESULTS[0] += 1
    result = dict(entry)
    result.update({
        "result_ordinal": RESULTS[0],
        "result_epoch": time.time(),
        "status": status,
        "output_hex": data.hex(),
        "decoded": _decode_state(data),
    })
    print("AKS_BKD_STATE_RETURN " + json.dumps(result, sort_keys=True),
          flush=True)
    bp.SetEnabled(False)
    PENDING.pop(bp.GetID(), None)
    if RESULTS[0] == 3:
        _write_success(result)
    return False


def _install(target, address, callback, label):
    bp = target.BreakpointCreateByAddress(address)
    bp.SetScriptCallbackFunction("aks_backboard_state_callbacks." + callback)
    print("COMMAND_LIST_PROOF id=%d label=%s address=0x%x locations=%d" %
          (bp.GetID(), label, address, bp.GetNumLocations()), flush=True)
    if bp.GetNumLocations() != 1:
        raise RuntimeError("%s expected one location, got %d" %
                           (label, bp.GetNumLocations()))


def install(debugger, slide):
    SLIDE[0] = slide
    target = debugger.GetSelectedTarget()
    _install(target, AKS_GET_EXTENDED_DEVICE_STATE + slide, "on_entry",
             "AKS_GET_EXTENDED_DEVICE_STATE")
    _install(target, AKS_GET_DEVICE_STATE + slide, "on_entry",
             "AKS_GET_DEVICE_STATE")
