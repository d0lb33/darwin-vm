"""Read native plugin names/results at verified migration-wrapper boundaries.

iOS 27 wrapper static 0x100003700 calls the plugin; 0x100003708 receives
its boolean result. Both retain the work block in x20. Its +0x28 field is
the plugin name used by the native signpost at 0x100003788. Pass a verified
PIE base, since unrelated processes can execute at the same virtual address.
"""
import json
import time

import bks_checkin_callbacks as bks
from inspect_install_coordinators import string

SITES = {}


def on_plugin(frame, bp_loc, _dict):
    process = frame.GetThread().GetProcess()
    if bks._progname(process) != "com.apple.migrationpluginwrapper":
        return False
    bp = bp_loc.GetBreakpoint()
    site = SITES[bp.GetID()]
    site["hits"] += 1

    class Reader:
        def read(self, address, size):
            raw = bks._read(process, address, size)
            if len(raw) != size:
                raise RuntimeError("unreadable plugin name at 0x%x" % address)
            return raw

    block = bks._reg(frame, "x20")
    name_pointer = bks._u64(process, block + 0x28)
    try:
        name = string(Reader(), name_pointer, bks.SLIDE[0] + 0x1e6f0c220) if name_pointer else "<null>"
    except (ValueError, RuntimeError) as error:
        name = str(error)
    event = {"label": site["label"], "time": time.time(),
             "hit": site["hits"], "plugin": name, "block": block,
             "name_pointer": name_pointer, "pc": bks._reg(frame, "pc"),
             "tpidr_el1": bks._reg(frame, "tpidr_el1")}
    if site["label"] == "MIGRATION_PLUGIN_RETURN":
        event["result"] = bks._reg(frame, "x0")
    print("MIGRATION_PLUGIN " + json.dumps(event, sort_keys=True), flush=True)
    bks._write_event("progress.%s.json" % site["label"], event)
    if site["hits"] >= 128:
        bp.SetEnabled(False)
    return False


def install(debugger, base):
    ids = []
    for offset, label in ((0x3700, "MIGRATION_PLUGIN_ENTRY"),
                          (0x3708, "MIGRATION_PLUGIN_RETURN")):
        bp = debugger.GetSelectedTarget().BreakpointCreateByAddress(base + offset)
        SITES[bp.GetID()] = {"label": label, "hits": 0}
        bp.SetScriptCallbackFunction("migration_plugin_callbacks.on_plugin")
        ids.append(bp.GetID())
    print("MIGRATION_PLUGIN_READY base=0x%x ids=%s" % (base, ids), flush=True)
    return ids
