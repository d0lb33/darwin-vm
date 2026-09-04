"""Scale SpringBoard-created FrontBoard watchdog provisions for slow probes.

RunningBoard's decoded termination reason proved that PurpleBuddy receives a
10-second *wall-clock* scene-create allowance while it gets only ~1--4 seconds
of guest CPU.  Patching the later terminate syscall is too late: FrontBoard
has already invalidated the scene.  This diagnostic callback runs at the final
positive duration load inside
``-[FBApplicationProcessWatchdogPolicy watchdogPolicyForProcess:eventContext:]``
and, when explicitly enabled, scales every positive duration constructed in
SpringBoard before its policy object is built.  It is deliberately off by
default because the boundary is not specific to Setup.

Static boundary (iOS 27 beta 8 shared cache): 0x1c49eb354.  The immediately
preceding instructions load the provision builder from ``sp+0x98`` and its
double duration from ``builder+0x18``; the immediately following instructions
compare that duration and construct the watchdog provision.
"""
import os
import struct
import time

import lldb


SLIDE = [0]
PROGNAME_PTR = 0x1e6ef1590
EXTEND = os.environ.get("DVM_EXTEND_FRONTBOARD_WATCHDOGS", "0") != "0"
SECONDS = float(os.environ.get("DVM_FRONTBOARD_WATCHDOG_SECS", "600"))
HITS = [0]


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
    data = _read(process, string, 64) if string else b""
    return (data.split(b"\0", 1)[0].decode("ascii", "replace")
            if data else "<unreadable>")


def on_duration(frame, _bp_loc, _dict):
    process = frame.GetThread().GetProcess()
    if _progname(process) != "SpringBoard":
        return False
    HITS[0] += 1
    builder = frame.FindRegister("x8").GetValueAsUnsigned()
    raw = _read(process, builder + 0x18, 8) if builder else b""
    before = struct.unpack("<d", raw)[0] if len(raw) == 8 else None
    written = 0
    success = False
    if (EXTEND and before is not None and before > 0 and before < SECONDS):
        error = lldb.SBError()
        written = process.WriteMemory(
            builder + 0x18, struct.pack("<d", SECONDS), error)
        success = error.Success() and written == 8
    print("WELCOME_FRONTBOARD_WATCHDOG_DURATION hit=%d t=%.3f "
          "builder=0x%x before=%r after=%r written=%d success=%d" %
          (HITS[0], time.time(), builder, before,
           SECONDS if success else before, written, int(success)),
          flush=True)
    return False


def install(debugger, slide):
    SLIDE[0] = int(slide)
    target = debugger.GetSelectedTarget()
    address = SLIDE[0] + 0x1c49eb354
    breakpoint = target.BreakpointCreateByAddress(address)
    breakpoint.SetScriptCallbackFunction(
        "frontboard_watchdog_callbacks.on_duration")
    print("WELCOME_FRONTBOARD_WATCHDOG_READY id=%d address=0x%x "
          "extend=%d seconds=%g" %
          (breakpoint.GetID(), address, int(EXTEND), SECONDS), flush=True)
