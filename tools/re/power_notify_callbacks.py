"""One-shot native libnotify delivery probe for an already-published IOPS source.

The caller must restore a checkpoint in which the process-scoped Darwin VM
InternalBattery publisher is still alive.  This script neither writes a source
dictionary nor makes a device-tree/IORegistry change: on a SpringBoard main
observer it calls the guest's existing `notify_post` export with IOKit's mapped
`com.apple.system.powersources.percent` literal, then restores every touched
register before execution resumes.
"""
import json
import struct
import time

import lldb
import welcome_abort_callbacks as names


SLIDE = 0
EVENT_PATH = None
CALL = None
POSTED = False

OBSERVER = 0x1806544e8
NOTIFY_POST = 0x2c21686b8
PERCENT_LITERAL = 0x18f092b2c
BC_QUERY = 0x2554d42ec
SB_CONNECTED_CHANGED = 0x2243f53c8
SB_UPDATE = 0x22468f430
SB_DEVICE_RETURN = 0x22468f45c


def _emit(kind, **fields):
    event = {"time": time.time(), "kind": kind, **fields}
    print("POWER_NOTIFY " + json.dumps(event, sort_keys=True), flush=True)
    if EVENT_PATH:
        with open(EVENT_PATH, "a", encoding="utf-8") as out:
            out.write(json.dumps(event, sort_keys=True) + "\n")


def _read(process, address, size):
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    if error.Fail() or len(data) != size:
        raise RuntimeError("read %#x: %s" % (address, error))
    return data


def _u64(process, address):
    return int.from_bytes(_read(process, address, 8), "little")


def _reg(frame, name):
    return frame.FindRegister(name).GetValueAsUnsigned()


def _set_data(frame, name, raw):
    error = lldb.SBError()
    data = lldb.SBData()
    data.SetData(error, raw, lldb.eByteOrderLittle, 8)
    register = frame.FindRegister(name)
    if error.Fail() or not register.SetData(data, error):
        raise RuntimeError("set " + name + ": " + str(error))


def _set(frame, name, value):
    raw = int(value & ((1 << (frame.FindRegister(name).GetByteSize() * 8)) - 1)).to_bytes(
        frame.FindRegister(name).GetByteSize(), "little")
    _set_data(frame, name, raw)


def _save_registers(frame):
    saved = {}
    for name in (["x%d" % index for index in range(29)] +
                 ["fp", "lr", "sp", "pc", "cpsr"] +
                 ["v%d" % index for index in range(32)] + ["fpsr", "fpcr"]):
        register = frame.FindRegister(name)
        if register.IsValid():
            error = lldb.SBError()
            saved[name] = register.GetData().ReadRawData(
                error, 0, register.GetByteSize())
            if error.Fail():
                raise RuntimeError("save " + name + ": " + str(error))
    return saved


def _on_native_return(frame, location, _dict):
    global CALL, POSTED
    if CALL is None:
        return False
    if (_reg(frame, "sp") != CALL["sp"] or
            _reg(frame, "tpidr_el1") != CALL["tp"]):
        return False
    result = _reg(frame, "x0") & 0xffffffff
    _emit("notify-return", result=result, result_hex="0x%08x" % result)
    for name, value in CALL["registers"].items():
        _set_data(frame, name, value)
    location.GetBreakpoint().SetEnabled(False)
    CALL = None
    POSTED = True
    _emit("registers-restored")
    return False


def _on_observer(frame, location, _dict):
    global CALL
    if CALL is not None or POSTED:
        return False
    process = frame.GetThread().GetProcess()
    if names._progname(process) != "SpringBoard":
        return False
    if not 0x160000000 <= _reg(frame, "sp") < 0x180000000:
        return False
    literal = PERCENT_LITERAL + SLIDE
    entry = NOTIFY_POST + SLIDE
    if _read(process, literal, len("com.apple.system.powersources.percent") + 1) != (
            b"com.apple.system.powersources.percent\0"):
        raise RuntimeError("mapped percent literal mismatch")
    if _read(process, entry, 4) != bytes.fromhex("7f2303d5"):
        raise RuntimeError("mapped notify_post entry mismatch")
    registers = _save_registers(frame)
    CALL = {"registers": registers, "sp": _reg(frame, "sp"),
            "tp": _reg(frame, "tpidr_el1"), "pc": _reg(frame, "pc")}
    return_bp = process.GetTarget().BreakpointCreateByAddress(CALL["pc"])
    return_bp.SetScriptCallbackFunction(__name__ + "._on_native_return")
    _set(frame, "x0", literal)
    _set(frame, "lr", CALL["pc"])
    _set(frame, "pc", entry)
    location.GetBreakpoint().SetEnabled(False)
    _emit("notify-call", entry=entry, literal=literal,
          thread=frame.GetThread().GetThreadID())
    return False


def _trace(frame, label):
    process = frame.GetThread().GetProcess()
    if names._progname(process) != "SpringBoard":
        return False
    event = {"pc": _reg(frame, "pc"), "thread": frame.GetThread().GetThreadID(),
             "x0": _reg(frame, "x0"), "x1": _reg(frame, "x1"),
             "sp": _reg(frame, "sp")}
    if label == "device-return":
        device = _reg(frame, "x0")
        event["device"] = device
        if device:
            event["is_internal"] = _read(process, device + 0x25, 1)[0]
            event["percent_object"] = _u64(process, device + 0x18)
    _emit(label, **event)
    return False


def on_bc_query(frame, _location, _dict):
    return _trace(frame, "bc-query")


def on_connected_devices_changed(frame, _location, _dict):
    return _trace(frame, "connected-devices-changed")


def on_status_update(frame, _location, _dict):
    return _trace(frame, "status-update")


def on_device_return(frame, _location, _dict):
    return _trace(frame, "device-return")


def install(debugger, slide, event_path):
    global SLIDE, EVENT_PATH, CALL, POSTED
    if CALL is not None:
        raise RuntimeError("native call already active")
    SLIDE = int(slide)
    EVENT_PATH = str(event_path)
    CALL = None
    POSTED = False
    names.SLIDE[0] = SLIDE
    target = debugger.GetSelectedTarget()
    for address, callback in ((BC_QUERY, "on_bc_query"),
                              (SB_CONNECTED_CHANGED, "on_connected_devices_changed"),
                              (SB_UPDATE, "on_status_update"),
                              (SB_DEVICE_RETURN, "on_device_return")):
        breakpoint = target.BreakpointCreateByAddress(address + SLIDE)
        breakpoint.SetScriptCallbackFunction(__name__ + "." + callback)
    breakpoint = target.BreakpointCreateByAddress(OBSERVER + SLIDE)
    breakpoint.SetScriptCallbackFunction(__name__ + "._on_observer")
    _emit("armed", observer=OBSERVER + SLIDE, notify_post=NOTIFY_POST + SLIDE,
          literal=PERCENT_LITERAL + SLIDE)
