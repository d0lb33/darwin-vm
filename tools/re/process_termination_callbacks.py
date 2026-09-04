"""Witness the process-termination syscalls behind Setup scene invalidation.

Setup repeatedly enters UIApplicationMain and disappears without reaching the
userspace exit(), abort(), or libc++ fatal boundaries.  These kernel syscall
entries are a bounded next layer: they distinguish self-exit, POSIX kill, and
termination-with-payload without changing guest behavior.  Addresses are the
positive-control output of ``ipsw kernel syscall firmware/bootkc`` for this
exact kernelcache; the normal 0x20000000 kernel slide is applied at install.
"""
import os
import time

import lldb


USER_SLIDE = [0]
KERNEL_SLIDE = int(os.environ.get("DVM_KERNEL_SLIDE", "0x20000000"), 0)
PROGNAME_PTR = 0x1e6ef1590
HITS = {}
LABELS = {}
LIMIT = int(os.environ.get("DVM_TERMINATION_TRACE_LIMIT", "96"), 0)
STOP_NAMESPACE = int(os.environ.get(
    "DVM_TERMINATION_STOP_NAMESPACE", "10"), 0)
STOP_CODE = int(os.environ.get(
    "DVM_TERMINATION_STOP_CODE", "0x8badf00d"), 0)
STOP_REASON_SUBSTRING = os.environ.get(
    "DVM_TERMINATION_STOP_REASON", "com.apple.purplebuddy")
BYPASS_SETUP_WATCHDOG = os.environ.get(
    "DVM_BYPASS_SETUP_LAUNCH_WATCHDOG", "0") != "0"
TRACE_ALL_SYSCALLS = os.environ.get(
    "DVM_TERMINATION_TRACE_ALL", "0") != "0"


def _read(process, address, size):
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    return data if error.Success() else b""


def _u64(process, address):
    data = _read(process, address, 8)
    return int.from_bytes(data, "little") if len(data) == 8 else 0


def _cstring(process, address, limit=2048):
    data = _read(process, address, limit) if address else b""
    return (data.split(b"\0", 1)[0].decode("utf-8", "replace")
            if data else "<unreadable>")


def _progname(process):
    pointer = _u64(process, USER_SLIDE[0] + PROGNAME_PTR)
    string = _u64(process, pointer) if pointer else 0
    data = _read(process, string, 96) if string else b""
    return (data.split(b"\0", 1)[0].decode("ascii", "replace")
            if data else "<unreadable>")


def on_syscall(frame, bp_loc, _dict):
    breakpoint = bp_loc.GetBreakpoint()
    label = LABELS.get(breakpoint.GetID(), "TERMINATION_SYSCALL")
    hit = HITS.get(breakpoint.GetID(), 0) + 1
    HITS[breakpoint.GetID()] = hit
    process = frame.GetThread().GetProcess()
    args = frame.FindRegister("x1").GetValueAsUnsigned()
    raw = _read(process, args, 64)
    values = []
    for name in ("x0", "x1", "x2", "x3", "x4", "x5", "x6", "x7",
                 "lr", "tpidr_el1"):
        values.append("%s=0x%x" %
                      (name, frame.FindRegister(name).GetValueAsUnsigned()))
    current_proc = frame.FindRegister("x0").GetValueAsUnsigned()
    current_procname = _cstring(process, current_proc + 0x55c, 32)
    decoded = {}
    if label == "KERNEL_TERMINATE_WITH_PAYLOAD" and len(raw) >= 56:
        decoded = {
            "target_pid": int.from_bytes(raw[0:4], "little", signed=True),
            "reason_namespace": int.from_bytes(raw[8:12], "little"),
            "reason_code": int.from_bytes(raw[16:24], "little"),
            "payload": int.from_bytes(raw[24:32], "little"),
            "payload_size": int.from_bytes(raw[32:40], "little"),
            "reason_string": int.from_bytes(raw[40:48], "little"),
            "reason_flags": int.from_bytes(raw[48:56], "little"),
        }
        decoded["reason_text"] = _cstring(
            process, decoded["reason_string"])
    print("WELCOME_%s hit=%d t=%.3f procname=%s progname=%s thread=0x%x "
          "%s args=%s decoded=%r" %
          (label, hit, time.time(), current_procname, _progname(process),
           frame.GetThread().GetThreadID(), " ".join(values), raw.hex(),
           decoded),
          flush=True)
    if hit >= LIMIT:
        breakpoint.SetEnabled(False)
        print("WELCOME_%s_BOUNDED_DISABLED after=%d" % (label, hit),
              flush=True)
    is_target = (decoded.get("reason_namespace") == STOP_NAMESPACE and
                 decoded.get("reason_code") == STOP_CODE and
                 STOP_REASON_SUBSTRING in decoded.get("reason_text", ""))
    if is_target:
        print("WELCOME_TERMINATION_TARGET_STOP namespace=%d code=0x%x "
              "target_pid=%d caller=%s reason=%r" %
              (STOP_NAMESPACE, STOP_CODE, decoded["target_pid"],
               current_procname, decoded["reason_text"]), flush=True)
        if BYPASS_SETUP_WATCHDOG:
            # Do not jump directly to LR here.  At this entry PSTATE.BTYPE
            # still describes the incoming BL, so changing PC to the caller
            # produces a kernel BTI failure.  Redirect the request to an
            # impossible positive PID and let the real syscall prologue,
            # lookup, epilogue, and return execute normally.
            error = lldb.SBError()
            written = process.WriteMemory(
                args, (0x7fffffff).to_bytes(4, "little"), error)
            print("WELCOME_SETUP_WATCHDOG_BYPASS target_pid=%d "
                  "replacement_pid=2147483647 written=%d success=%d" %
                  (decoded["target_pid"], written, int(error.Success())),
                  flush=True)
            return False
        return True
    return False


def on_user_terminate(frame, _bp_loc, _dict):
    process = frame.GetThread().GetProcess()
    caller = _progname(process)
    reason = _cstring(
        process, frame.FindRegister("x5").GetValueAsUnsigned())
    if caller != "runningboardd" or STOP_REASON_SUBSTRING not in reason:
        return False
    values = []
    for name in ("x0", "x1", "x2", "x3", "x4", "x5", "x6", "lr",
                 "sp", "tpidr_el1"):
        values.append("%s=0x%x" %
                      (name, frame.FindRegister(name).GetValueAsUnsigned()))
    print("WELCOME_USER_TERMINATE_TARGET caller=%s t=%.3f %s reason=%r" %
          (caller, time.time(), " ".join(values), reason), flush=True)
    thread = frame.GetThread()
    for index in range(min(thread.GetNumFrames(), 64)):
        current = thread.GetFrameAtIndex(index)
        pc = current.GetPC() & 0x0000ffffffffffff
        static = (pc - USER_SLIDE[0]
                  if 0x180000000 + USER_SLIDE[0] <= pc <
                  0x340000000 + USER_SLIDE[0] else 0)
        print("WELCOME_USER_TERMINATE_FRAME index=%d pc=0x%x static=%s "
              "fp=0x%x sp=0x%x" %
              (index, pc, "0x%x" % static if static else "<outside-cache>",
               current.GetFP(), current.GetSP()), flush=True)
    return True


def _install(target, address, label):
    breakpoint = target.BreakpointCreateByAddress(address)
    breakpoint.AddName(label)
    LABELS[breakpoint.GetID()] = label
    breakpoint.SetScriptCallbackFunction(
        "process_termination_callbacks.on_syscall")
    print("WELCOME_%s_READY id=%d address=0x%x limit=%d" %
          (label, breakpoint.GetID(), address, LIMIT), flush=True)


def install(debugger, user_slide):
    USER_SLIDE[0] = int(user_slide)
    target = debugger.GetSelectedTarget()
    # static VAs from the bootkc's BSD syscall table.
    entries = [(0xfffffff00b019ea0, "KERNEL_TERMINATE_WITH_PAYLOAD")]
    if TRACE_ALL_SYSCALLS:
        entries[0:0] = [
            (0xfffffff00aff6bd8, "KERNEL_EXIT"),
            (0xfffffff00b0195c8, "KERNEL_KILL"),
        ]
    for static, label in entries:
        _install(target, static + KERNEL_SLIDE, label)
    user_terminate = USER_SLIDE[0] + 0x237ce3118
    breakpoint = target.BreakpointCreateByAddress(user_terminate)
    breakpoint.SetScriptCallbackFunction(
        "process_termination_callbacks.on_user_terminate")
    print("WELCOME_USER_TERMINATE_READY id=%d address=0x%x" %
          (breakpoint.GetID(), user_terminate), flush=True)
