"""Bounded checkpoints for Setup's first scene connection.

Unlike shared-cache callbacks, Setup's executable has its own per-launch ASLR
slide.  Pass that image slide (runtime ``UIApplicationMain`` return address
minus static ``0x100005d74``) to :func:`install`.  The checkpoints cover the
entry, both sides of the only block-dispatch call, the end of URL-context
enumeration, the final scene attachment call, and the method return.  They
answer in one live guest whether UIKit reaches BuddySceneDelegate and, if so,
which bounded portion fails to complete.

Addresses are from the iOS 27 beta 8 ``/Applications/Setup.app/Setup`` binary.
"""
import time

import lldb


PROGNAME_PTR = 0x1e6ef1590
SHARED_CACHE_SLIDE = [0]
CONFIG = {}
HITS = {}


CHECKPOINTS = (
    (0x1000bfa9c, "SETUP_SCENE_WILL_CONNECT_ENTRY"),
    (0x1000bfc4c, "SETUP_SCENE_BLOCK_DISPATCH_PRE"),
    (0x1000bfc50, "SETUP_SCENE_BLOCK_DISPATCH_RETURN"),
    (0x1000bfde8, "SETUP_SCENE_URL_ENUMERATION_DONE"),
    (0x1000bfe9c, "SETUP_SCENE_ATTACH_PRE"),
    (0x1000bfea0, "SETUP_SCENE_ATTACH_RETURN"),
    (0x1000bff44, "SETUP_SCENE_WILL_CONNECT_RETURN"),
)


def _read(process, address, size):
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    return data if error.Success() else b""


def _u64(process, address):
    data = _read(process, address, 8)
    return int.from_bytes(data, "little") if len(data) == 8 else 0


def _progname(process):
    pointer = _u64(process, SHARED_CACHE_SLIDE[0] + PROGNAME_PTR)
    string = _u64(process, pointer) if pointer else 0
    data = _read(process, string, 64) if string else b""
    return (data.split(b"\0", 1)[0].decode("ascii", "replace")
            if data else "<unreadable>")


def on_checkpoint(frame, bp_loc, _dict):
    process = frame.GetThread().GetProcess()
    if _progname(process) != "Setup":
        return False
    breakpoint = bp_loc.GetBreakpoint()
    label = CONFIG[breakpoint.GetID()]
    hit = HITS.get(breakpoint.GetID(), 0) + 1
    HITS[breakpoint.GetID()] = hit
    print("WELCOME_%s hit=%d t=%.3f thread=0x%x pc=0x%x lr=0x%x "
          "x0=0x%x x1=0x%x x2=0x%x x3=0x%x" %
          (label, hit, time.time(), frame.GetThread().GetThreadID(),
           frame.GetPC(), frame.FindRegister("lr").GetValueAsUnsigned(),
           frame.FindRegister("x0").GetValueAsUnsigned(),
           frame.FindRegister("x1").GetValueAsUnsigned(),
           frame.FindRegister("x2").GetValueAsUnsigned(),
           frame.FindRegister("x3").GetValueAsUnsigned()), flush=True)
    if hit >= 4:
        breakpoint.SetEnabled(False)
    return False


def install(debugger, shared_cache_slide, setup_image_slide):
    SHARED_CACHE_SLIDE[0] = int(shared_cache_slide)
    target = debugger.GetSelectedTarget()
    created = []
    for static, label in CHECKPOINTS:
        address = int(setup_image_slide) + static
        breakpoint = target.BreakpointCreateByAddress(address)
        breakpoint.SetScriptCallbackFunction(
            "setup_scene_callbacks.on_checkpoint")
        CONFIG[breakpoint.GetID()] = label
        created.append((breakpoint.GetID(), label, address))
    print("WELCOME_SETUP_SCENE_READY shared_cache_slide=0x%x "
          "setup_image_slide=0x%x breakpoints=%d" %
          (int(shared_cache_slide), int(setup_image_slide), len(created)),
          flush=True)
    for identifier, label, address in created:
        print("WELCOME_SETUP_SCENE_BREAKPOINT id=%d label=%s address=0x%x" %
              (identifier, label, address), flush=True)
