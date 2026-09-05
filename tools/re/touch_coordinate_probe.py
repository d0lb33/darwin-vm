"""Bounded read-only coordinate/hit witness for 24A5430a QEMU input tests."""
import json
import struct
import time
import welcome_abort_callbacks as names

SLIDE = 0
PATH = ''
COUNTS = {}
POINTS = {0x22a414e48: 'backboard-hit', 0x184e40dc0: 'window-hit',
          0x1c4b0c820: 'icon-down', 0x1c4b0ec04: 'icon-tap'}


def hit(frame, location, _dict):
    import lldb
    address = location.GetAddress().GetLoadAddress(frame.GetThread().GetProcess().GetTarget()) - SLIDE
    COUNTS[address] = COUNTS.get(address, 0) + 1
    result = dict(time=time.time(), event=POINTS[address],
                  process=names._progname(frame.GetThread().GetProcess()),
                  object=frame.FindRegister('x0').GetValueAsUnsigned())
    for n in ('d0', 'd1'):
        error = lldb.SBError()
        raw = frame.FindRegister(n).GetData().ReadRawData(error, 0, 8)
        if error.Success():
            result[n] = struct.unpack('<d', raw)[0]
    with open(PATH, 'a') as out:
        out.write(json.dumps(result) + '\n')
    print('TOUCH_COORDINATE ' + json.dumps(result), flush=True)
    if COUNTS[address] >= 12:
        location.GetBreakpoint().SetEnabled(False)
    return False


def install(debugger, slide, path):
    global SLIDE, PATH
    SLIDE, PATH = slide, path
    names.SLIDE[0] = slide
    for address in POINTS:
        bp = debugger.GetSelectedTarget().BreakpointCreateByAddress(address + slide)
        bp.SetScriptCallbackFunction(__name__ + '.hit')
