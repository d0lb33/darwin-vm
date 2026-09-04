"""Host-LLDB-only, bounded DCP state acknowledgement experiment.

Attach to a paused QEMU, import this module, then call install(debugger).
Only observed 12-byte interface-0 requests are acknowledged on their own
endpoint: open 2 -> ack 3, close 4 -> ack 5. The guest receiver at bootkc
0xfffffff008b9267c distinguishes requests from acknowledgements: receiving 2
installs a firmware-side type-2 desired-state-3 claim; receiving 3 only acks
the outstanding open. Receiving 4 drops that claim and sends 5; receiving 5
clears the outstanding close. Echoing requests creates a spurious peer claim.
A duplicate phase or unexpected message stops the host for inspection.
"""
import json
import struct
import time

import lldb

SEEN = {}
LAST = {}
REQUEST_LIMIT = 132
REQUEST_COUNT = 0
EVENT_PATH = "/tmp/dvm/host-dcp-state-probe.jsonl"


def _event(**fields):
    fields["time"] = time.time()
    line = json.dumps(fields, sort_keys=True)
    print("HOST_DCP_STATE " + line, flush=True)
    with open(EVENT_PATH, "a") as stream:
        stream.write(line + "\n")


def _eval(frame, expression):
    options = lldb.SBExpressionOptions()
    options.SetIgnoreBreakpoints(True)
    options.SetTimeoutInMicroSeconds(3000000)
    value = frame.EvaluateExpression(expression, options)
    if value.GetError().Fail():
        raise RuntimeError(value.GetError().GetCString())
    return value


def on_request(frame, _location, _dict):
    global REQUEST_COUNT
    try:
        values = {name: frame.FindVariable(name).GetValueAsUnsigned()
                  for name in ("opaque", "ep", "channel", "type", "data", "len")}
        error = lldb.SBError()
        raw = frame.GetThread().GetProcess().ReadMemory(values["data"], 12, error)
        if error.Fail() or len(raw) != 12:
            raise RuntimeError("cannot read request")
        seq, iface, length, state = struct.unpack("<HHII", raw)
        key = (values["ep"], state)
        fields = dict(event="request", **values, raw=raw.hex(), seq=seq,
                      iface=iface, payload_length=length, state=state)
        if (iface != 0 or length != 4 or state not in (2, 4)
                or values["channel"] != 0 or values["type"] != 0
                or LAST.get(values["ep"]) == state
                or REQUEST_COUNT >= REQUEST_LIMIT):
            _event(**fields, action="stop-unmodelled-or-repeated")
            return True
        if not _eval(frame, "bql_locked()").GetValueAsUnsigned():
            raise RuntimeError("request callback does not own QEMU BQL")
        reply = bytearray(raw)
        reply[8:12] = struct.pack("<I", state + 1)
        sent = _eval(frame,
                     "({ unsigned char reply[12] = {%s}; "
                     "darwin_afk_send_qe(((DarwinDCP *)0x%x)->afk, %d, 0, 0, "
                     "reply, 12, true); })" %
                     (",".join(str(b) for b in reply),
                      values["opaque"], values["ep"]))
        if not sent.GetValueAsUnsigned():
            raise RuntimeError("AFK ring rejected acknowledgement")
        SEEN[key] = raw.hex()
        LAST[values["ep"]] = state
        REQUEST_COUNT += 1
        _event(**fields, action="acknowledge-request", reply=reply.hex())
        return False
    except Exception as error:
        _event(event="error", error=str(error))
        return True


def install(debugger):
    debugger.HandleCommand("process handle SIGUSR2 -s false -n false -p true")
    debugger.HandleCommand("process handle SIGUSR1 -s false -n false -p true")
    target = debugger.GetSelectedTarget()
    bp = target.BreakpointCreateByName("dcp_afk_recv")
    bp.SetCondition("len == 12 && channel == 0 && type == 0")
    bp.SetScriptCallbackFunction(__name__ + ".on_request")
    if bp.GetNumLocations() != 1:
        raise RuntimeError("dcp_afk_recv must resolve to exactly one location")
    print("HOST_DCP_STATE_READY breakpoint=%d" % bp.GetID(), flush=True)
