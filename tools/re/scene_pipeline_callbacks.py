"""Trace the concrete FrontBoardServices scene-creation pipeline.

All sites are real implementations (not protocol declarations) in the iOS 27
beta 8 shared cache.  The process-name filter keeps the system-wide sites
bounded to SpringBoard and Setup, so the trace can remain installed while a
live launch proceeds without materially increasing debugger traffic.
"""
import time

import lldb


SLIDE = [0]
PROGNAME_PTR = 0x1e6ef1590
CONFIG = {}
HITS = {}


SITES = (
    (0x1a84301f8, "FBS_SCENES_CLIENT_REQUEST"),
    (0x1a8423960, "FBS_SCENES_CLIENT_CREATE"),
    (0x1a845cbe8, "FBS_SCENES_CLIENT_ACTIVATE_FUTURE"),
    (0x1a84280d0, "FBS_WORKSPACE_REQUEST"),
    (0x1a8426390, "FBS_SCENE_DID_CREATE_CALLOUT"),
    (0x1a84722f8, "FBS_CLIENT_AGENT_DID_INITIALIZE"),
    (0x1a841947c, "FBS_HOST_AGENT_DID_INITIALIZE"),
    (0x1a8422b54, "FBS_SCENE_SEND_UPDATE"),
)


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


def on_site(frame, bp_loc, _dict):
    process = frame.GetThread().GetProcess()
    name = _progname(process)
    if name not in {"SpringBoard", "Setup"}:
        return False
    breakpoint = bp_loc.GetBreakpoint()
    label = CONFIG[breakpoint.GetID()]
    hit = HITS.get(breakpoint.GetID(), 0) + 1
    HITS[breakpoint.GetID()] = hit
    registers = []
    for register in ("x0", "x1", "x2", "x3", "x4", "x5", "x6", "x7",
                     "lr", "sp", "tpidr_el1"):
        registers.append("%s=0x%x" %
                         (register, frame.FindRegister(
                             register).GetValueAsUnsigned()))
    print("WELCOME_SCENE_PIPELINE label=%s hit=%d t=%.3f progname=%s "
          "thread=0x%x %s" %
          (label, hit, time.time(), name, frame.GetThread().GetThreadID(),
           " ".join(registers)), flush=True)
    if hit >= 16:
        breakpoint.SetEnabled(False)
    return False


def install(debugger, slide):
    SLIDE[0] = int(slide)
    target = debugger.GetSelectedTarget()
    for static, label in SITES:
        address = SLIDE[0] + static
        breakpoint = target.BreakpointCreateByAddress(address)
        breakpoint.SetScriptCallbackFunction(
            "scene_pipeline_callbacks.on_site")
        CONFIG[breakpoint.GetID()] = label
        print("WELCOME_SCENE_PIPELINE_BREAKPOINT id=%d label=%s address=0x%x" %
              (breakpoint.GetID(), label, address), flush=True)
    print("WELCOME_SCENE_PIPELINE_READY slide=0x%x breakpoints=%d" %
          (SLIDE[0], len(SITES)), flush=True)
