"""Read-only, bounded internal-IOPS liveness query at an EL0 Mach boundary.

Calls the native 24A5430a
IOPSCopyPowerSourcesByTypePrecise(kIOPSSourceInternal, &out) and
CFArrayGetCount through their verified shared-cache exports.  Its precise ABI
has only two caller arguments: type in w0 and non-null CFArrayRef * in x1;
the function sets x2 itself before creating its XPC request.  It changes no
power dictionary and restores the interrupted register set before resuming.
The caller must stop the VM externally if powerd does not reply.
"""
import json
import time
import lldb
import welcome_abort_callbacks as names

SLIDE = 0
EVENT_PATH = None
CALL = None
DONE = False

MACH_MSG2_TRAP = 0x237ccfccc
IOPS_COPY_PRECISE = 0x18f004698
CFARRAY_COUNT = 0x18060f174
CFRELEASE = 0x180600c6c
SOURCE_INTERNAL = 0


def _emit(kind, **fields):
    event = {"time": time.time(), "kind": kind, **fields}
    print("POWER_IOPS_LIVE " + json.dumps(event, sort_keys=True), flush=True)
    if EVENT_PATH:
        with open(EVENT_PATH, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")


def _reg(frame, name):
    return frame.FindRegister(name).GetValueAsUnsigned()


def _set(frame, name, value):
    register = frame.FindRegister(name)
    assert register.IsValid(), name
    assert register.SetValueFromCString(hex(value & ((1 << register.GetByteSize() * 8) - 1))), name


def _save(frame):
    result = {}
    for name in (["x%d" % n for n in range(29)] + ["fp", "lr", "sp", "pc", "cpsr"] +
                 ["v%d" % n for n in range(32)] + ["fpsr", "fpcr"]):
        reg = frame.FindRegister(name)
        if reg.IsValid():
            error = lldb.SBError()
            result[name] = reg.GetData().ReadRawData(error, 0, reg.GetByteSize())
            if error.Fail():
                raise RuntimeError("save %s: %s" % (name, error))
    return result


def _restore(frame, saved):
    for name, raw in saved.items():
        error = lldb.SBError()
        data = lldb.SBData()
        data.SetData(error, raw, lldb.eByteOrderLittle, 8)
        reg = frame.FindRegister(name)
        if error.Fail() or not reg.SetData(data, error):
            raise RuntimeError("restore %s: %s" % (name, error))


def _restore_output(process, call):
    error = lldb.SBError()
    if process.WriteMemory(call["out"], call["out_original"], error) != 8 or error.Fail():
        raise RuntimeError("restore output slot: %s" % error)


def _return(frame, location, _dict):
    global CALL, DONE
    if CALL is None or _reg(frame, "sp") != CALL["sp"] or _reg(frame, "tpidr_el1") != CALL["tp"]:
        return False
    process = frame.GetThread().GetProcess()
    target = process.GetTarget()
    result = _reg(frame, "x0")
    if CALL["phase"] == "copy":
        output = process.ReadMemory(CALL["out"], 8, lldb.SBError())
        CALL["array"] = int.from_bytes(output, "little") if len(output) == 8 else 0
        _emit("copy-return", ioreturn=result, ioreturn_hex="0x%08x" % (result & 0xffffffff),
              array=CALL["array"], nonnull=bool(CALL["array"]))
        if result or not CALL["array"]:
            _restore_output(process, CALL)
            _restore(frame, CALL["saved"])
            location.GetBreakpoint().SetEnabled(False)
            CALL = None
            DONE = True
            _emit("registers-restored", count=None)
            return False
        CALL["phase"] = "count"
        _set(frame, "x0", CALL["array"])
        _set(frame, "lr", CALL["pc"])
        _set(frame, "pc", CFARRAY_COUNT + SLIDE)
        return False
    if CALL["phase"] == "count":
        CALL["count"] = result
        _emit("count-return", count=result, array=CALL["array"])
        CALL["phase"] = "release"
        _set(frame, "x0", CALL["array"])
        _set(frame, "lr", CALL["pc"])
        _set(frame, "pc", CFRELEASE + SLIDE)
        return False
    if CALL["phase"] == "release":
        _emit("release-return")
        _restore_output(process, CALL)
        _restore(frame, CALL["saved"])
        location.GetBreakpoint().SetEnabled(False)
        CALL = None
        DONE = True
        _emit("registers-restored")
    return False


def _on_mach_msg(frame, location, _dict):
    global CALL
    if CALL is not None or DONE or names._progname(frame.GetThread().GetProcess()) != "SpringBoard":
        return False
    if not 0x160000000 <= _reg(frame, "sp") < 0x180000000:
        return False
    process = frame.GetThread().GetProcess()
    error = lldb.SBError()
    entry = process.ReadMemory(IOPS_COPY_PRECISE + SLIDE, 4, error)
    if error.Fail() or len(entry) != 4:
        raise RuntimeError("unreadable IOPS export entry: %s" % error)
    saved = _save(frame)
    output_slot = _reg(frame, "sp")
    output_original = process.ReadMemory(output_slot, 8, error)
    if error.Fail() or len(output_original) != 8:
        raise RuntimeError("unreadable caller output slot: %s" % error)
    CALL = {"saved": saved, "sp": output_slot, "tp": _reg(frame, "tpidr_el1"),
            "out": output_slot, "out_original": output_original,
            "pc": _reg(frame, "pc"), "phase": "copy"}
    return_bp = process.GetTarget().BreakpointCreateByAddress(CALL["pc"])
    return_bp.SetScriptCallbackFunction(__name__ + "._return")
    _set(frame, "x0", SOURCE_INTERNAL)
    _set(frame, "x1", output_slot)
    _set(frame, "lr", CALL["pc"])
    _set(frame, "pc", IOPS_COPY_PRECISE + SLIDE)
    location.GetBreakpoint().SetEnabled(False)
    _emit("copy-call", entry=IOPS_COPY_PRECISE + SLIDE, entry_bytes=entry.hex(), source_type=SOURCE_INTERNAL,
          thread=frame.GetThread().GetThreadID())
    return False


def install(debugger, slide, event_path):
    global SLIDE, EVENT_PATH, CALL, DONE
    SLIDE, EVENT_PATH, CALL, DONE = int(slide), str(event_path), None, False
    names.SLIDE[0] = SLIDE
    target = debugger.GetSelectedTarget()
    breakpoint = target.BreakpointCreateByAddress(MACH_MSG2_TRAP + SLIDE)
    breakpoint.SetScriptCallbackFunction(__name__ + "._on_mach_msg")
    _emit("armed", mach_msg2=MACH_MSG2_TRAP + SLIDE, copy=IOPS_COPY_PRECISE + SLIDE,
          array_count=CFARRAY_COUNT + SLIDE)
