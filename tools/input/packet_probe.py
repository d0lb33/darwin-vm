"""Bounded read-only v4 helper framing witness; use its verified image slide.

Build native-home-v4: _input_loop+0xa8, 0x10000424c, insn 0x79414be8,
immediately after fgets; the 160-byte local record is at raw SP+0xa4.
"""
import json
import time
import bootstrap_helper

PATH = ''
COUNT = 0


def hit(frame, location, _dict):
    global COUNT
    p = frame.GetThread().GetProcess()
    raw = bootstrap_helper.calls.read(p, frame.FindRegister('sp').GetValueAsUnsigned()+0xa4, 160)
    raw = raw.split(b'\0', 1)[0]
    if raw == b'\n':
        return False
    COUNT += 1
    record = dict(time=time.time(), record=raw.decode('ascii', 'replace'), hex=raw.hex())
    with open(PATH, 'a') as stream:
        stream.write(json.dumps(record)+'\n')
    print('NATIVE_INPUT_WIRE '+json.dumps(record), flush=True)
    if COUNT >= 12:
        location.GetBreakpoint().SetEnabled(False)
    return False


def install(debugger, image_slide, path):
    global PATH, COUNT
    PATH, COUNT = path, 0
    bp = debugger.GetSelectedTarget().BreakpointCreateByAddress(0x10000424c+image_slide)
    bp.SetScriptCallbackFunction(__name__+'.hit')
    print('NATIVE_INPUT_WIRE_ARMED', bp.GetID(), flush=True)
