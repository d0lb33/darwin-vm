"""Opt-in, per-plugin TCG timing diagnostic; never skip migration work.

DataMigrationPlugin ivars verified in com.apple.datamigrator ObjC metadata:
name +0x18, timeIntervalBeforeWatchdog +0x28, timeIntervalBeforeReboot +0x30.
Observe _performOneMigrationWithParameters:watchdogCoordinator:needsRetry:
at static 0x100013224, before it creates either dispatch timer.
R14's native panic names SpringBoard.migrator while snapshot encoding advances.
"""
import json
import struct
import time

import lldb
import bks_checkin_callbacks as bks
from inspect_install_coordinators import string

BASE = 0
EXTEND = False
SECONDS = 3600.0
NAMES = {"SpringBoard.migrator", "SpringBoard"}
HITS = 0


def on_start(frame, bp_loc, _dict):
    global HITS
    process = frame.GetThread().GetProcess()
    if bks._progname(process) != "com.apple.datamigrator":
        return False
    obj = bks._reg(frame, "x0")
    raw = bks._read(process, obj, 0x50)
    if len(raw) != 0x50 or int.from_bytes(raw[:8], "little") & 0x7fffffffff8 != BASE + 0x30b20:
        print("MIGRATION_DEADLINE unexpected plugin object 0x%x" % obj, flush=True)
        return True

    class Reader:
        def read(self, address, size):
            data = bks._read(process, address, size)
            if len(data) != size:
                raise RuntimeError("unreadable plugin name")
            return data

    name_pointer = int.from_bytes(raw[0x18:0x20], "little")
    try:
        name = string(Reader(), name_pointer, bks.SLIDE[0] + 0x1e6f0c220)
    except (ValueError, RuntimeError) as error:
        name = str(error)
    HITS += 1
    before = struct.unpack("<dd", raw[0x28:0x38])
    event = {"label": "MIGRATION_DEADLINE", "time": time.time(),
             "plugin": name, "object": obj, "before": before,
             "applied": False, "pc": bks._reg(frame, "pc")}
    if EXTEND and name in NAMES and all(0 <= x < SECONDS for x in before):
        # Zero disables the earlier watchdog. Preserve that native choice;
        # only lengthen an enabled allowance and keep reboot later than it.
        after = (SECONDS, SECONDS * 2) if before[0] else (0.0, SECONDS)
        error = lldb.SBError()
        written = process.WriteMemory(obj + 0x28, struct.pack("<dd", *after), error)
        event.update(after=after, written=written,
                     applied=error.Success() and written == 16)
        if not event["applied"]:
            print("MIGRATION_DEADLINE " + json.dumps(event), flush=True)
            return True
    print("MIGRATION_DEADLINE " + json.dumps(event, sort_keys=True), flush=True)
    bks._write_event("progress.MIGRATION_DEADLINE.json", event)
    if event["applied"]:
        bks._write_event("diagnostic.MIGRATION_DEADLINE.%s.json" % name, event)
    if HITS >= 128:
        bp_loc.GetBreakpoint().SetEnabled(False)
    return name in NAMES and not event["applied"]


def install(debugger, base):
    global BASE
    BASE = base
    bp = debugger.GetSelectedTarget().BreakpointCreateByAddress(base + 0x13224)
    bp.SetScriptCallbackFunction("migration_deadline_callbacks.on_start")
    print("MIGRATION_DEADLINE_READY id=%d extend=%s names=%s" %
          (bp.GetID(), EXTEND, sorted(NAMES)), flush=True)
    return bp.GetID()
