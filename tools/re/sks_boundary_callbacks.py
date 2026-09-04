"""Read-only pre-SKS checkpoints for the iOS 27 generated op10 wrapper.

At kernel static 0xfffffff00957be80, x2 points to the in-memory request
and class is u32 at +0x74 (wire +0x60). Stop before transmission so the
checkpoint can be replayed with a changed model without a pending timeout.
"""
import json
import lldb
import bks_checkin_callbacks as bks

STOP_CLASSES = {13}
SEEN = set()
PENDING = {}
STOP_AFTER_CLASSES = set()


def op10_before(frame, bp_loc, _dict):
    address = frame.FindRegister("x2").GetValueAsUnsigned()
    process = frame.GetThread().GetProcess()
    error = lldb.SBError()
    raw = process.ReadMemory(address + 0x74, 4, error)
    if not error.Success() or len(raw) != 4:
        print("SKS_OP10_CLASS_UNREADABLE: stopped before sending")
        return True
    protection_class = int.from_bytes(raw, "little")
    if protection_class == 13:
        thread = frame.FindRegister("tpidr_el1").GetValueAsUnsigned()
        PENDING[thread] = protection_class
    stop = protection_class in STOP_CLASSES
    if protection_class not in SEEN or stop:
        SEEN.add(protection_class)
        payload = {
            "label": "SKS_OP10_BEFORE", "class": protection_class,
            "request": address, "progname": bks._progname(process),
            "pc": frame.FindRegister("pc").GetValueAsUnsigned(),
            "stop": stop,
        }
        print("TRACE_JSON " + json.dumps(payload, sort_keys=True))
        bks._write_event("progress.SKS_OP10_BEFORE_CLASS%d.json" %
                         protection_class, payload)
    return stop


def op10_after(frame, bp_loc, _dict):
    thread = frame.FindRegister("tpidr_el1").GetValueAsUnsigned()
    protection_class = PENDING.pop(thread, None)
    if protection_class is None:
        return False
    process = frame.GetThread().GetProcess()
    sp = frame.FindRegister("sp").GetValueAsUnsigned()
    # Generated variant-2 output fields at static 0xfffffff00957be84.
    error = lldb.SBError()
    raw = process.ReadMemory(sp + 0xe8, 0x54, error)
    payload = {
        "label": "SKS_OP10_AFTER", "class": protection_class,
        "status": frame.FindRegister("x0").GetValueAsUnsigned(),
        "progname": bks._progname(process),
        "output": raw.hex() if error.Success() else "unreadable",
    }
    if error.Success() and len(raw) == 0x54:
        payload["key_lengths"] = [int.from_bytes(raw[o:o + 4], "little")
                                  for o in (8, 24)]
        payload["class_scalars"] = [int.from_bytes(raw[o:o + 4], "little")
                                    for o in (0x4c, 0x50)]
    print("TRACE_JSON " + json.dumps(payload, sort_keys=True))
    bks._write_event("progress.SKS_OP10_AFTER_CLASS%d.json" %
                     protection_class, payload)
    return protection_class in STOP_AFTER_CLASSES
