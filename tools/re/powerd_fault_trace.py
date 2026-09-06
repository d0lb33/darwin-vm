#!/usr/bin/env python3
"""Stop on powerd's first EL0 data abort and preserve the saved fault state.

This is a narrowly scoped diagnostic for the iPhone17,3 24A5430a boot kernel.
It breaks at the kernel's ``sleh_synchronous`` entry, not at a guessed
userspace address.  XNU receives the ``arm_context_t *`` in x0 there; its
``arm_saved_state64`` stores the interrupted EL0 PC, SP, FAR, ESR and x0-x28.
The callback filters for a powerd data abort at virtual address 0x20 and
returns True only for that event, leaving QEMU paused at the kernel boundary.

Run inside LLDB attached to the stopped QEMU gdbstub::

  command script import /tmp/dvm/power-dt-worktree/tools/re/powerd_fault_trace.py
  script powerd_fault_trace.run(lldb.debugger, "127.0.0.1", 1602, 90, \
      "/tmp/dvm/POWERD_FAULT_DIAG1/powerd-fault.jsonl", 0x1bf90000)

The final argument is the measured shared-cache slide used only to read
libsystem_c's per-process ``___progname_pointer``.  The callback records an
unverified low-address candidate if that pointer cannot be read; it never
stops for an unverified process.

Address derivation:
* XNU 24A source ``osfmk/arm64/sleh.c:sleh_synchronous`` has the signature
  ``(arm_context_t *context, uint64_t esr, vm_offset_t far, bool)``.
* The matching 24A bootkc has the ``Invalid SVC_64 context`` and
  ``Exception on 2-byte instruction`` strings at code xrefs in the function
  beginning at static VA ``0xfffffff00ac6548c``.  Its first instructions save
  x0..x3, matching that source signature.
* ``osfmk/mach/arm/thread_status.h`` defines arm_saved_state64: x0 starts at
  context+0x08, pc at +0x108, cpsr at +0x110, far at +0x118, and esr at +0x120.
"""

import json
import time
from pathlib import Path

import lldb


SLEH_SYNCHRONOUS_STATIC = 0xfffffff00ac6548c
DEFAULT_KERNEL_SLIDE = 0x20000000
PROGNAME_POINTER_STATIC = 0x1e6ef1590
ESR_EC_DABORT_EL0 = 0x24
PSR64_MODE_EL_MASK = 0x0c
SAVED_X0 = 0x08
SAVED_PC = 0x108
SAVED_CPSR = 0x110
SAVED_FAR = 0x118
SAVED_ESR = 0x120

TRACE = {
    "kernel_slide": DEFAULT_KERNEL_SLIDE,
    "user_slide": 0,
    "output": None,
    "stream": None,
    "target": "powerd",
    "found": False,
    "hits": 0,
    "candidates": 0,
    "breakpoint": None,
}


def _register(frame, name):
    value = frame.FindRegister(name)
    return value.GetValueAsUnsigned() if value and value.IsValid() else 0


def _read(process, address, size):
    if not address or address < 0x1000:
        return b""
    error = lldb.SBError()
    try:
        data = process.ReadMemory(address, size, error)
    except Exception:
        return b""
    return bytes(data) if error.Success() else b""


def _u64(process, address):
    data = _read(process, address, 8)
    return int.from_bytes(data, "little") if len(data) == 8 else 0


def _u32(process, address):
    data = _read(process, address, 4)
    return int.from_bytes(data, "little") if len(data) == 4 else 0


def _progname(process):
    pointer = _u64(process, TRACE["user_slide"] + PROGNAME_POINTER_STATIC)
    string = _u64(process, pointer) if pointer else 0
    data = _read(process, string, 96) if string else b""
    return (data.split(b"\0", 1)[0].decode("ascii", "replace")
            if data else "<unreadable>")


def _record(record):
    stream = TRACE.get("stream")
    if stream is None:
        return
    stream.write(json.dumps(record, sort_keys=True) + "\n")
    stream.flush()


def _saved_context(process, context):
    return {
        "context": "0x%x" % context,
        "x": ["0x%x" % _u64(process, context + SAVED_X0 + 8 * index)
              for index in range(29)],
        "fp": "0x%x" % _u64(process, context + SAVED_X0 + 29 * 8),
        "lr": "0x%x" % _u64(process, context + SAVED_X0 + 30 * 8),
        "sp": "0x%x" % _u64(process, context + SAVED_X0 + 31 * 8),
        "pc": "0x%x" % _u64(process, context + SAVED_PC),
        "cpsr": "0x%x" % _u32(process, context + SAVED_CPSR),
        "far": "0x%x" % _u64(process, context + SAVED_FAR),
        "saved_esr": "0x%x" % _u64(process, context + SAVED_ESR),
    }


def on_synchronous(frame, _bp_loc, _internal_dict):
    """LLDB callback: return True only for the selected fatal powerd abort."""
    TRACE["hits"] += 1
    process = frame.GetThread().GetProcess()
    context = _register(frame, "x0")
    entry_esr = _register(frame, "x1")
    entry_far = _register(frame, "x2")
    exception_class = (entry_esr >> 26) & 0x3f
    if exception_class != ESR_EC_DABORT_EL0 or not context:
        return False

    saved = _saved_context(process, context)
    cpsr = int(saved["cpsr"], 16)
    far = int(saved["far"], 16)
    # XNU's PSR64_IS_USER is ((cpsr & 0x0c) == 0); preserve that exact test.
    if (cpsr & PSR64_MODE_EL_MASK) != 0:
        return False

    name = _progname(process)
    record = {
        "event": "user_data_abort",
        "time_ns": time.time_ns(),
        "kernel_pc": "0x%x" % frame.GetPC(),
        "thread": "0x%x" % frame.GetThread().GetThreadID(),
        "tpidr_el1": "0x%x" % _register(frame, "tpidr_el1"),
        "entry_esr": "0x%x" % entry_esr,
        "entry_far": "0x%x" % entry_far,
        "exception_class": "0x%x" % exception_class,
        "progname": name,
        "saved": saved,
    }
    if far == 0x20:
        TRACE["candidates"] += 1
        record["low_address_candidate"] = True
        _record(record)
        print("POWERD_FAULT_CANDIDATE name=%s pc=%s far=%s esr=%s" %
              (name, saved["pc"], saved["far"], saved["saved_esr"]), flush=True)
    if name == TRACE["target"] and far == 0x20:
        TRACE["found"] = True
        record["verified_target"] = True
        _record(record)
        print("POWERD_FAULT_STOP verified name=powerd fault_pc=%s far=%s "
              "context=%s" % (saved["pc"], saved["far"], saved["context"]),
              flush=True)
        return True
    return False


def install(debugger, user_slide, kernel_slide=DEFAULT_KERNEL_SLIDE,
            output=None, target="powerd"):
    """Install the static 24A exception breakpoint on an already-stopped VM."""
    TRACE.update({"kernel_slide": int(kernel_slide), "user_slide": int(user_slide),
                  "target": target, "found": False, "hits": 0, "candidates": 0})
    if output is not None:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        TRACE["stream"] = path.open("w", encoding="utf-8")
        TRACE["output"] = str(path)
    target_object = debugger.GetSelectedTarget()
    address = SLEH_SYNCHRONOUS_STATIC + TRACE["kernel_slide"]
    breakpoint = target_object.BreakpointCreateByAddress(address)
    if not breakpoint.IsValid() or breakpoint.GetNumLocations() != 1:
        raise RuntimeError("could not set sleh_synchronous breakpoint at 0x%x" % address)
    breakpoint.SetScriptCallbackFunction("powerd_fault_trace.on_synchronous")
    TRACE["breakpoint"] = breakpoint
    _record({"event": "installed", "kernel_static": "0x%x" % SLEH_SYNCHRONOUS_STATIC,
             "kernel_runtime": "0x%x" % address,
             "kernel_slide": "0x%x" % TRACE["kernel_slide"],
             "user_slide": "0x%x" % TRACE["user_slide"],
             "saved_context_offsets": {"pc": "0x108", "cpsr": "0x110",
                                       "far": "0x118", "esr": "0x120"}})
    print("POWERD_FAULT_READY id=%d static=0x%x runtime=0x%x user_slide=0x%x" %
          (breakpoint.GetID(), SLEH_SYNCHRONOUS_STATIC, address,
           TRACE["user_slide"]), flush=True)


def run(debugger, host, port, duration, output, user_slide,
        kernel_slide=DEFAULT_KERNEL_SLIDE):
    """Attach, run up to ``duration`` seconds, and leave a verified hit paused.

    No explicit Detach is issued: QEMU's gdbstub resumes a target on detach.
    A verified callback already leaves the target stopped.  If no target event
    occurs, the caller must HMP-stop the VM before closing the debugger.
    """
    debugger.SetAsync(True)
    target = debugger.CreateTargetWithFileAndArch("", "arm64")
    error = lldb.SBError()
    listener = debugger.GetListener()
    process = target.ConnectRemote(listener, "connect://%s:%d" % (host, int(port)),
                                   "gdb-remote", error)
    if not error.Success() or not process or not process.IsValid():
        raise RuntimeError("gdb remote attach failed: %s" %
                           (error.GetCString() or "unknown error"))
    install(debugger, int(user_slide), int(kernel_slide), output)
    if process.GetState() == lldb.eStateStopped:
        error = process.Continue()
        if not error.Success():
            raise RuntimeError("initial continue failed: %s" % error.GetCString())
    deadline = time.monotonic() + min(int(duration), 90)
    while time.monotonic() < deadline:
        event = lldb.SBEvent()
        if not listener.WaitForEvent(1, event):
            continue
        if not lldb.SBProcess.EventIsProcessEvent(event):
            continue
        state = lldb.SBProcess.GetStateFromEvent(event)
        if TRACE["found"]:
            _record({"event": "verified_stop", "state": int(state),
                     "hits": TRACE["hits"], "candidates": TRACE["candidates"]})
            print("POWERD_FAULT_RESULT verified-stop; QEMU remains paused", flush=True)
            return True
        if state == lldb.eStateStopped:
            error = process.Continue()
            if not error.Success():
                raise RuntimeError("continue after unrelated stop failed: %s" %
                                   error.GetCString())
    _record({"event": "timeout", "hits": TRACE["hits"],
             "candidates": TRACE["candidates"]})
    print("POWERD_FAULT_RESULT timeout; HMP stop before debugger teardown", flush=True)
    return False
