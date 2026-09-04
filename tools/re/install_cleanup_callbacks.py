"""Bounded native installation-cleanup observations for checkpoint replays.

Pass an instruction-verified installcoordinationd image base and cache slide.
The static sites and R10 evidence are in dcp-state-acknowledgements.md. These
callbacks only observe; they never release groups or skip migration work.
Use bks_checkin_callbacks.SUCCESS_LABELS / STOP_ON_SUCCESS to choose a stop.
"""
import json
import time

import bks_checkin_callbacks as bks
from inspect_install_coordinators import string

PLACEHOLDER_HITS = 0


def on_placeholder(frame, bp_loc, _dict):
    global PLACEHOLDER_HITS
    process = frame.GetThread().GetProcess()
    if bks._progname(process) != "com.apple.migrationpluginwrapper":
        return False

    class Reader:
        def read(self, address, size):
            raw = bks._read(process, address, size)
            if len(raw) != size:
                raise RuntimeError("unreadable NSString at 0x%x" % address)
            return raw

    PLACEHOLDER_HITS += 1
    address = bks._reg(frame, "x2")
    try:
        bundle = string(Reader(), address)
    except (ValueError, RuntimeError) as error:
        bundle = str(error)
    event = {"label": "IX_PLACEHOLDER_ENTRY", "time": time.time(),
             "hit": PLACEHOLDER_HITS, "bundle_id": bundle,
             "bundle_object": address, "pc": bks._reg(frame, "pc"),
             "lr": bks._reg(frame, "lr"),
             "install_type": bks._reg(frame, "x4")}
    print("INSTALL_PLACEHOLDER " + json.dumps(event, sort_keys=True), flush=True)
    bks._write_event("progress.IX_PLACEHOLDER_ENTRY.json", event)
    if PLACEHOLDER_HITS >= 64:
        bp_loc.GetBreakpoint().SetEnabled(False)
    return False


def install_placeholders(debugger, slide):
    """Observe the measured removable-system-app placeholder entry, x2=bundle ID."""
    bks.SLIDE[0] = int(slide)
    bp = debugger.GetSelectedTarget().BreakpointCreateByAddress(slide + 0x1c22c17ac)
    bp.SetScriptCallbackFunction("install_cleanup_callbacks.on_placeholder")
    print("INSTALL_PLACEHOLDER_READY id=%d" % bp.GetID(), flush=True)
    return bp.GetID()


def install(debugger, base, slide, removal=False):
    bks.SLIDE[0] = int(slide)
    target = debugger.GetSelectedTarget()
    interpreter = debugger.GetCommandInterpreter()
    sites = [
        (base + 0x4a47c, "IC_PENDING_INSTALLS_COMPLETED", "x0 x20 x23", (),
         "installcoordinationd", 16),
        (base + 0x3a488, "IC_LS_QUEUE_LEAVE", "x0 x19",
         (("group", "x0", 0x30, 8), ("block", "x19", 0x20, 16)),
         "installcoordinationd", 32),
        (base + 0x3a348, "IC_UNINSTALL_QUEUE_LEAVE", "x0",
         (("group", "x0", 0x30, 8),), "installcoordinationd", 32),
        (slide + 0x1c22981e8, "IX_CANCEL_RETURN", "x0 x19 x20", (),
         "com.apple.migrationpluginwrapper", 16),
    ]
    if removal:
        sites.append((slide + 0x1c22e9ec8, "IC_REMOVEFILE_RETURN",
                      "x0 x19 x20 x21 x22", (), "installcoordinationd", 32))
    ids = []
    for address, label, registers, reads, process, limit in sites:
        bks._install(target, interpreter, address, label, registers, reads, limit)
        bp = target.GetBreakpointAtIndex(target.GetNumBreakpoints() - 1)
        bks.CONFIG[bp.GetID()]["allowed"] = (process,)
        ids.append(bp.GetID())
    print("INSTALL_CLEANUP_READY base=0x%x slide=0x%x ids=%s" %
          (base, slide, ids), flush=True)
    return ids
