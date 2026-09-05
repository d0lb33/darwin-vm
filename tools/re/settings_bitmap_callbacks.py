"""Read-only LLDB stops for Settings' IconServices bitmap path.

Use only against a paused checkpoint whose dyld shared-cache slide is known.
The callbacks read registers and process memory only. Factory hits continue so
a later nil-CGImage trap can be correlated to the same guest thread.
"""
import lldb


STATIC_FACTORY = 0x1D41E3DB0
STATIC_TRAP = 0x1D41E3EA0
# libsystem's ___progname indirection; its value is char **, so read twice.
STATIC_PROGNAME_POINTER = 0x1E6EF1590


def _unsigned(frame, name):
    value = frame.FindRegister(name)
    return value.GetValueAsUnsigned() if value.IsValid() else 0


def _double(frame, name):
    error = lldb.SBError()
    value = frame.FindRegister(name).GetData().GetDouble(error, 0)
    if not error.Success():
        raise RuntimeError("cannot read floating register %s: %s" % (name, error))
    return value


def _pointer(process, address):
    error = lldb.SBError()
    value = process.ReadPointerFromMemory(address, error)
    return value if error.Success() else 0


def _progname(process):
    pointer_to_pointer = _pointer(process, _progname_pointer_runtime)
    character_pointer = _pointer(process, pointer_to_pointer) if pointer_to_pointer else 0
    if not character_pointer:
        return "<unreadable>"
    error = lldb.SBError()
    value = process.ReadCStringFromMemory(character_pointer, 256, error)
    if not error.Success() or not value:
        return "<unreadable>"
    return value.replace("\n", " ")


def _sample(frame):
    process = frame.GetThread().GetProcess()
    tpidr = _unsigned(frame, "tpidr_el1")
    # `_IconServices_SwiftUI` preserves its target UIImage in x21 from
    # 0x1d41e3d00 through the factory call at 0x1d41e3db0.  Reading its isa
    # is evidence only; no Objective-C message is sent into the guest.
    target = _unsigned(frame, "x21")
    return {
        "gdb_process_id": process.GetProcessID(),
        "guest_progname": _progname(process),
        "thread_id": frame.GetThread().GetThreadID(),
        "tpidr_el1": tpidr,
        "pc": _unsigned(frame, "pc"),
        "x0": _unsigned(frame, "x0"),
        "x1": _unsigned(frame, "x1"),
        "x2": _unsigned(frame, "x2"),
        "target_uiimage": target,
        "target_isa": _pointer(process, target) if target else 0,
        "d0": _double(frame, "d0"),
        "d1": _double(frame, "d1"),
        "d2": _double(frame, "d2"),
    }


def _describe(sample):
    return ("prog=%s gdb-process-id=%d thread=0x%x tpidr_el1=0x%x pc=0x%x "
            "logical-width=%.17g logical-height=%.17g scale=%.17g "
            "receiver=0x%x selector=0x%x source-cgimage=0x%x "
            "target-uiimage=0x%x target-isa=0x%x" % (
                sample["guest_progname"], sample["gdb_process_id"], sample["thread_id"],
                sample["tpidr_el1"], sample["pc"], sample["d0"], sample["d1"],
                sample["d2"], sample["x0"], sample["x1"], sample["x2"],
                sample["target_uiimage"], sample["target_isa"]))


def factory_capture(frame, _location, _internal_dict):
    sample = _sample(frame)
    _last_factory[sample["tpidr_el1"]] = sample
    print("SETTINGS_BITMAP_FACTORY " + _describe(sample), flush=True)
    return False


def trap_capture(frame, _location, _internal_dict):
    sample = _sample(frame)
    prior = _last_factory.get(sample["tpidr_el1"])
    print("SETTINGS_BITMAP_NIL_CGIMAGE_TRAP " + _describe(sample), flush=True)
    print("SETTINGS_BITMAP_PRIOR_FACTORY %s" %
          (_describe(prior) if prior else "none-for-tpidr"), flush=True)
    return True


def install(debugger, slide):
    """Log all factory calls and stop at the first nil-CGImage trap."""
    global _progname_pointer_runtime
    _progname_pointer_runtime = STATIC_PROGNAME_POINTER + slide
    target = debugger.GetSelectedTarget()
    factory = target.BreakpointCreateByAddress(STATIC_FACTORY + slide)
    factory.SetScriptCallbackFunction(__name__ + ".factory_capture")
    trap = target.BreakpointCreateByAddress(STATIC_TRAP + slide)
    trap.SetOneShot(True)
    trap.SetScriptCallbackFunction(__name__ + ".trap_capture")
    print("SETTINGS_BITMAP_PROBE_READY read_only=1 slide=0x%x factory=0x%x trap=0x%x progname-pointer=0x%x" %
          (slide, STATIC_FACTORY + slide, STATIC_TRAP + slide,
           _progname_pointer_runtime), flush=True)


_last_factory = {}
_progname_pointer_runtime = 0
