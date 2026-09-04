"""Diagnose SpringBoard's first fatal Welcome-path exception.

This is intentionally a one-boundary, same-boot probe.  It filters the shared
libsystem_c abort entry by ``___progname_pointer`` so crashes from unrelated
daemons do not consume another long boot or obscure SpringBoard's failure.
"""
import lldb
import os


SLIDE = [0]
PROGNAME_PTR = 0x1e6ef1590
BYPASS_INVALID_INDICATOR = os.environ.get(
    "DVM_BYPASS_INVALID_SECURE_INDICATOR", "0") != "0"
BYPASS_SWITCHER_LAYOUT_ASSERT = os.environ.get(
    "DVM_BYPASS_SWITCHER_LAYOUT_ASSERT", "0") != "0"
INVALID_INDICATOR_HITS = [0]
FATAL_PROCESSES = {"SpringBoard", "Setup"}


def _read(process, address, size):
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    return data if error.Success() else b""


def _u64(process, address):
    data = _read(process, address, 8)
    return int.from_bytes(data, "little") if len(data) == 8 else 0


def _progname(process):
    pointer = _u64(process, SLIDE[0] + PROGNAME_PTR)
    string = _u64(process, pointer) if pointer else 0
    data = _read(process, string, 96) if string else b""
    return data.split(b"\0", 1)[0].decode("ascii", "replace") if data else "<unknown>"


def _cstring(process, address, limit=512):
    data = _read(process, address, limit) if address else b""
    return (data.split(b"\0", 1)[0].decode("utf-8", "replace")
            if data else "<unreadable>")


def on_abort_message(frame, _bp_loc, _dict):
    process = frame.GetThread().GetProcess()
    name = _progname(process)
    if name not in FATAL_PROCESSES:
        return False
    registers = {
        name: frame.FindRegister(name).GetValueAsUnsigned()
        for name in ("x0", "x1", "x2", "x3", "x4", "x5", "x6", "x7")
    }
    print("WELCOME_ABORT_MESSAGE progname=%s format=%r" %
          (name, _cstring(process, registers["x0"])), flush=True)
    for name, value in registers.items():
        print("WELCOME_ABORT_ARG %s=0x%x string=%r" %
              (name, value, _cstring(process, value)), flush=True)
    thread = frame.GetThread()
    for index in range(min(thread.GetNumFrames(), 64)):
        current = thread.GetFrameAtIndex(index)
        print("WELCOME_ABORT_MESSAGE_FRAME index=%d pc=0x%x fp=0x%x sp=0x%x" %
              (index, current.GetPC() & 0x0000ffffffffffff,
               current.GetFP(), current.GetSP()), flush=True)
    return True


def on_abort(frame, _bp_loc, _dict):
    process = frame.GetThread().GetProcess()
    name = _progname(process)
    if name not in FATAL_PROCESSES:
        return False
    thread = frame.GetThread()
    print("WELCOME_ABORT progname=%s thread=0x%x tpidr_el1=0x%x" %
          (name, thread.GetThreadID(),
           frame.FindRegister("tpidr_el1").GetValueAsUnsigned()), flush=True)
    for index in range(min(thread.GetNumFrames(), 64)):
        current = thread.GetFrameAtIndex(index)
        pc = current.GetPC() & 0x0000ffffffffffff
        print("WELCOME_ABORT_FRAME index=%d pc=0x%x fp=0x%x sp=0x%x" %
              (index, pc, current.GetFP(), current.GetSP()), flush=True)
    return True


def on_exit(frame, _bp_loc, _dict):
    process = frame.GetThread().GetProcess()
    name = _progname(process)
    if name != "Setup":
        return False
    thread = frame.GetThread()
    code = frame.FindRegister("w0").GetValueAsUnsigned()
    print("WELCOME_SETUP_EXIT code=%d thread=0x%x" %
          (code, thread.GetThreadID()), flush=True)
    for index in range(min(thread.GetNumFrames(), 64)):
        current = thread.GetFrameAtIndex(index)
        print("WELCOME_SETUP_EXIT_FRAME index=%d pc=0x%x fp=0x%x sp=0x%x" %
              (index, current.GetPC() & 0x0000ffffffffffff,
               current.GetFP(), current.GetSP()), flush=True)
    return True


def on_setupapp_return(frame, bp_loc, _dict):
    process = frame.GetThread().GetProcess()
    if _progname(process) != "SpringBoard":
        return False
    result = frame.FindRegister("x0").GetValueAsUnsigned()
    print("WELCOME_SETUPAPP_RETURN result=0x%x thread=0x%x lr=0x%x" %
          (result, frame.GetThread().GetThreadID(),
           frame.FindRegister("lr").GetValueAsUnsigned()), flush=True)
    # Eight identical nil returns are enough to distinguish a missing Setup
    # application from a later activation failure without flooding LLDB.
    breakpoint = bp_loc.GetBreakpoint()
    if breakpoint.GetHitCount() >= 8:
        breakpoint.SetEnabled(False)
    return False


def on_uiapplication_main(frame, _bp_loc, _dict):
    process = frame.GetThread().GetProcess()
    name = _progname(process)
    if name not in {"SpringBoard", "Setup", "AccessibilityUIServer",
                    "InputUI", "PosterBoard"}:
        return False
    print("WELCOME_UIAPPLICATION_MAIN progname=%s thread=0x%x x0=0x%x" %
          (name, frame.GetThread().GetThreadID(),
           frame.FindRegister("x0").GetValueAsUnsigned()), flush=True)
    return False


def on_invalid_indicator_return(frame, _bp_loc, _dict):
    process = frame.GetThread().GetProcess()
    if (_progname(process) != "SpringBoard" or
            frame.FindRegister("w0").GetValueAsUnsigned() != 0xffffffff):
        return False
    INVALID_INDICATOR_HITS[0] += 1
    sp = frame.GetSP()
    name = _u64(process, sp)
    raw = _read(process, name, 64) if name else b""
    print("WELCOME_INVALID_INDICATOR hit=%d object=0x%x raw=%s" %
          (INVALID_INDICATOR_HITS[0], name, raw.hex()), flush=True)
    # NSString subclasses vary.  Preserve every pointer-like word and a
    # bounded candidate string so a constant, heap, or tagged representation
    # can be distinguished without executing guest Objective-C code.
    for offset in range(0, len(raw) - 7, 8):
        value = int.from_bytes(raw[offset:offset + 8], "little")
        candidate = _read(process, value, 128)
        if candidate:
            print("WELCOME_INVALID_INDICATOR_WORD offset=0x%x value=0x%x "
                  "bytes=%s string=%r" %
                  (offset, value, candidate[:64].hex(),
                   candidate.split(b"\0", 1)[0].decode("utf-8", "replace")),
                  flush=True)
    if BYPASS_INVALID_INDICATOR:
        changed = frame.FindRegister("w0").SetValueFromCString("0")
        print("WELCOME_INVALID_INDICATOR_BYPASS from=0xffffffff to=0 "
              "applied=%d" % int(changed), flush=True)
        return False
    return True


def on_switcher_layout_assert(frame, _bp_loc, _dict):
    process = frame.GetThread().GetProcess()
    if _progname(process) != "SpringBoard":
        return False
    print("WELCOME_SWITCHER_LAYOUT_ASSERT thread=0x%x sp=0x%x "
          "transition=0x%x coordinator=0x%x bypass=%d" %
          (frame.GetThread().GetThreadID(), frame.GetSP(),
           _u64(process, frame.GetSP() + 0x58),
           _u64(process, frame.GetSP() + 0x88),
           int(BYPASS_SWITCHER_LAYOUT_ASSERT)), flush=True)
    if BYPASS_SWITCHER_LAYOUT_ASSERT:
        destination = SLIDE[0] + 0x2242d6580
        changed = frame.FindRegister("pc").SetValueFromCString(hex(destination))
        print("WELCOME_SWITCHER_LAYOUT_ASSERT_BYPASS destination=0x%x "
              "applied=%d" % (destination, int(changed)), flush=True)
        return False
    return True


def install(debugger, slide):
    SLIDE[0] = int(slide)
    target = debugger.GetSelectedTarget()
    address = SLIDE[0] + 0x187f727a4
    breakpoint = target.BreakpointCreateByAddress(address)
    breakpoint.SetScriptCallbackFunction("welcome_abort_callbacks.on_abort")
    print("WELCOME_ABORT_READY id=%d address=0x%x" %
          (breakpoint.GetID(), address), flush=True)
    setupapp_return = SLIDE[0] + 0x2248af350
    breakpoint = target.BreakpointCreateByAddress(setupapp_return)
    breakpoint.SetScriptCallbackFunction(
        "welcome_abort_callbacks.on_setupapp_return")
    print("WELCOME_SETUPAPP_RETURN_READY id=%d address=0x%x" %
          (breakpoint.GetID(), setupapp_return), flush=True)
    uiapplication_main = SLIDE[0] + 0x18490d11c
    breakpoint = target.BreakpointCreateByAddress(uiapplication_main)
    breakpoint.SetScriptCallbackFunction(
        "welcome_abort_callbacks.on_uiapplication_main")
    print("WELCOME_UIAPPLICATION_MAIN_READY id=%d address=0x%x" %
          (breakpoint.GetID(), uiapplication_main), flush=True)
    abort_message = SLIDE[0] + 0x2bfe70d18
    breakpoint = target.BreakpointCreateByAddress(abort_message)
    breakpoint.SetScriptCallbackFunction(
        "welcome_abort_callbacks.on_abort_message")
    print("WELCOME_ABORT_MESSAGE_READY id=%d address=0x%x" %
          (breakpoint.GetID(), abort_message), flush=True)
    invalid_indicator_return = SLIDE[0] + 0x184568fe0
    breakpoint = target.BreakpointCreateByAddress(invalid_indicator_return)
    breakpoint.SetScriptCallbackFunction(
        "welcome_abort_callbacks.on_invalid_indicator_return")
    print("WELCOME_INVALID_INDICATOR_READY id=%d address=0x%x bypass=%d" %
          (breakpoint.GetID(), invalid_indicator_return,
           int(BYPASS_INVALID_INDICATOR)), flush=True)
    exit_address = SLIDE[0] + 0x237ce21a4
    breakpoint = target.BreakpointCreateByAddress(exit_address)
    breakpoint.SetScriptCallbackFunction("welcome_abort_callbacks.on_exit")
    print("WELCOME_SETUP_EXIT_READY id=%d address=0x%x" %
          (breakpoint.GetID(), exit_address), flush=True)
    switcher_assert = SLIDE[0] + 0x2242d6548
    breakpoint = target.BreakpointCreateByAddress(switcher_assert)
    breakpoint.SetScriptCallbackFunction(
        "welcome_abort_callbacks.on_switcher_layout_assert")
    print("WELCOME_SWITCHER_LAYOUT_ASSERT_READY id=%d address=0x%x "
          "bypass=%d" %
          (breakpoint.GetID(), switcher_assert,
           int(BYPASS_SWITCHER_LAYOUT_ASSERT)), flush=True)
