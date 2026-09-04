"""Read the native FairPlay service's startup IOKit transaction.

R17 observes fairplayd.H2 opening com_apple_driver_FairPlayIOKit and issuing
selectors 22, 23, then 21 before main returns zero. All three IOReturns are
zero, so retain the actual in/out structures rather than infer success from
the transport status. No guest state is modified.
"""
import time

import bks_checkin_callbacks as bks

PENDING = {}
STOP = True


def entry(frame, bp_loc, _dict):
    process = frame.GetThread().GetProcess()
    if bks._progname(process) != "fairplayd.H2" or bks._reg(frame, "x1") != 21:
        return False
    sp = bks._reg(frame, "sp")
    output, size_pointer = bks._u64(process, sp), bks._u64(process, sp + 8)
    size = bks._u64(process, size_pointer) if size_pointer else 0
    input_pointer, input_size = bks._reg(frame, "x4"), bks._reg(frame, "x5")
    if input_size > 0x1000 or size > 0x1000:
        print("FAIRPLAY_BOUNDARY refuses oversized structure", flush=True)
        return True
    event = dict(time=time.time(), selector=21, input_pointer=input_pointer,
                 input_size=input_size, output=output, size_pointer=size_pointer,
                 capacity=size, tpidr=bks._reg(frame, "tpidr_el1"),
                 input_before=bks._read(process, input_pointer, input_size).hex())
    bp = process.GetTarget().BreakpointCreateByAddress(bks._reg(frame, "lr"))
    bp.SetScriptCallbackFunction("fairplay_boundary_callbacks.returned")
    PENDING[bp.GetID()] = event
    bp_loc.GetBreakpoint().SetEnabled(False)
    bks._write_event("progress.FAIRPLAY_SELECTOR21_ENTRY.json", event)
    return False


def returned(frame, bp_loc, _dict):
    bp = bp_loc.GetBreakpoint()
    event = PENDING[bp.GetID()]
    if bks._reg(frame, "tpidr_el1") != event["tpidr"]:
        return False
    process = frame.GetThread().GetProcess()
    event.update(return_time=time.time(), result=bks._reg(frame, "x0"))
    size = bks._u64(process, event["size_pointer"]) if event["size_pointer"] else 0
    event["output_size"] = size
    if size <= min(event["capacity"], 0x1000):
        event["output_bytes"] = bks._read(process, event["output"], size).hex()
    event["input_after"] = bks._read(process, event["input_pointer"], event["input_size"]).hex()
    bks._write_event("progress.FAIRPLAY_SELECTOR21_RETURN.json", event)
    print("FAIRPLAY_SELECTOR21_RETURN " + repr(event), flush=True)
    bp.SetEnabled(False)
    return STOP


def install(debugger):
    bp = debugger.GetSelectedTarget().BreakpointCreateByAddress(0x18efd97cc + bks.SLIDE[0])
    bp.SetScriptCallbackFunction("fairplay_boundary_callbacks.entry")
    return bp.GetID()
