"""Freeze the live guest when a selected process next enters mach_msg2_trap.

This is a cheap process-context sampler for a scene-create stall.  It avoids a
RAM scan: the shared-cache syscall boundary is system-wide, but the callback
reads ``___progname_pointer`` and ignores every process except ``TARGET``
before it prints the userspace frame chain. Repeated continues sample
additional threads in the same boot.
"""
import time

import lldb


SLIDE = [0]
PROGNAME_PTR = 0x1e6ef1590
MACH_MSG2_TRAP = 0x237ccfccc
HITS = [0]
TARGET = "Setup"
SEEN_THREADS = set()
UNIQUE_THREADS = True


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


def on_mach_msg(frame, bp_loc, _dict):
    process = frame.GetThread().GetProcess()
    if _progname(process) != TARGET:
        return False
    thread_pointer = frame.FindRegister("tpidr_el1").GetValueAsUnsigned()
    if UNIQUE_THREADS and thread_pointer in SEEN_THREADS:
        return False
    SEEN_THREADS.add(thread_pointer)
    HITS[0] += 1
    thread = frame.GetThread()
    print("WELCOME_PROCESS_MACH_MSG target=%s hit=%d t=%.3f thread=0x%x "
          "tpidr_el1=0x%x x0=0x%x x1=0x%x" %
          (TARGET, HITS[0], time.time(), thread.GetThreadID(),
           thread_pointer,
           frame.FindRegister("x0").GetValueAsUnsigned(),
           frame.FindRegister("x1").GetValueAsUnsigned()), flush=True)
    for index in range(min(thread.GetNumFrames(), 64)):
        current = thread.GetFrameAtIndex(index)
        print("WELCOME_PROCESS_MACH_MSG_FRAME target=%s index=%d pc=0x%x fp=0x%x "
              "sp=0x%x" %
              (TARGET, index, current.GetPC() & 0x0000ffffffffffff,
               current.GetFP(), current.GetSP()), flush=True)
    if HITS[0] >= 8:
        bp_loc.GetBreakpoint().SetEnabled(False)
    return True


def install(debugger, slide):
    SLIDE[0] = int(slide)
    target = debugger.GetSelectedTarget()
    address = SLIDE[0] + MACH_MSG2_TRAP
    breakpoint = target.BreakpointCreateByAddress(address)
    breakpoint.SetScriptCallbackFunction(
        "setup_stall_callbacks.on_mach_msg")
    print("WELCOME_PROCESS_MACH_MSG_READY target=%s id=%d address=0x%x" %
          (TARGET, breakpoint.GetID(), address), flush=True)
