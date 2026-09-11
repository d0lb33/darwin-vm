"""Generic ObjC/function entry tracer for the QEMU guest over the gdbstub.

Breaks at a list of unslid addresses and logs, for each hit in a target process,
the label plus x0..x3 (self, _cmd/arg, arg, arg). Non-invasive: it never edits
registers or memory, just logs and continues. Use it to watch which methods in a
subsystem actually fire during a boot (e.g. the lock-screen wallpaper poster
render path) and where the sequence stops.

Config via env TRACE_SPEC = JSON: {"progname": "SpringBoard",
  "points": [[unslid_addr, "label"], ...], "max_per": 40}
Install: trace_methods.install(lldb.debugger, slide)
Output lines: TRACE <label> proc=<name> x0=.. x1=.. x2=.. x3=..
"""
import json
import os
import lldb
import welcome_abort_callbacks as names

SLIDE = 0
SPEC = {}
COUNTS = {}


def _reg(frame, name):
    v = frame.FindRegister(name)
    return v.GetValueAsUnsigned() if v.IsValid() else 0


def on_break(frame, location, _dict):
    process = frame.GetThread().GetProcess()
    want = SPEC.get("progname")
    name = names._progname(process)
    if want and name != want:
        return False
    bpid = location.GetBreakpoint().GetID()
    label = SPEC["labels"].get(bpid, "bp%d" % bpid)
    COUNTS[label] = COUNTS.get(label, 0) + 1
    if COUNTS[label] > SPEC.get("max_per", 40):
        if COUNTS[label] == SPEC.get("max_per", 40) + 1:
            print("TRACE %s proc=%s (silenced after %d)" %
                  (label, name, SPEC.get("max_per", 40)), flush=True)
        return False
    print("TRACE %s proc=%s x0=%#x x1=%#x x2=%#x x3=%#x" %
          (label, name, _reg(frame, "x0"), _reg(frame, "x1"),
           _reg(frame, "x2"), _reg(frame, "x3")), flush=True)
    return False


def install(debugger, slide):
    global SLIDE, SPEC
    SLIDE = slide
    names.SLIDE[0] = slide
    SPEC = json.loads(os.environ["TRACE_SPEC"])
    SPEC["labels"] = {}
    target = debugger.GetSelectedTarget()
    for addr, label in SPEC["points"]:
        a = (addr if isinstance(addr, int) else int(addr, 0)) + slide
        bp = target.BreakpointCreateByAddress(a)
        bp.SetScriptCallbackFunction("trace_methods.on_break")
        SPEC["labels"][bp.GetID()] = label
        print("TRACE_READY bp=%d %s at %#x" % (bp.GetID(), label, a), flush=True)
    print("TRACE_INSTALLED %d points, progname=%s" %
          (len(SPEC["points"]), SPEC.get("progname")), flush=True)
