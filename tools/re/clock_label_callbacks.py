"""Disposable-restore test of CoverSheetKit's existing UIKit time label.

24A5430a +[CSProminentTextElementView textLabelClass] selects a SwiftUI
CSTitleElementViewAdapter for its time element. Redirect its taken adapter
branch (0x1b38d1394) to the existing _UIAnimatingLabel branch (0x1b38d13b4).
This preserves native initialization, formatting and layout. No other feature
flags or code bytes are changed; remove the breakpoint to end the experiment.
"""
import json
import lldb
import welcome_abort_callbacks as names

SLIDE = 0
PATH = None

def fallback(frame, _location, _dict):
    if names._progname(frame.GetThread().GetProcess()) != 'SpringBoard':
        return False
    row = dict(pc=hex(frame.GetPC()), destination=hex(0x1b38d13b4 + SLIDE),
               receiver=hex(frame.FindRegister('x19').GetValueAsUnsigned()))
    assert frame.FindRegister('pc').SetValueFromCString(row['destination'])
    print('CLOCK_NATIVE_LABEL ' + json.dumps(row), flush=True)
    with open(PATH, 'a') as f:
        f.write(json.dumps(row) + '\n')
    return False

def install(debugger, slide, path):
    global SLIDE, PATH
    SLIDE, PATH = int(slide), str(path)
    names.SLIDE[0] = SLIDE
    bp = debugger.GetSelectedTarget().BreakpointCreateByAddress(0x1b38d1394 + SLIDE)
    bp.SetScriptCallbackFunction(__name__ + '.fallback')


def no_glass(frame, _location, _dict):
    if names._progname(frame.GetThread().GetProcess()) != 'SpringBoard':
        return False
    # The leaf capability getter has executed mov w0,#1, but not ret.
    # Its base class returns zero at 0x1b38d6874. Let the caller perform
    # its ordinary vibrant-style selection and cleanup with that answer.
    row = dict(test='glass-capability', pc=hex(frame.GetPC()),
               result_before=frame.FindRegister('w0').GetValueAsUnsigned())
    assert row['result_before'] == 1
    assert frame.FindRegister('w0').SetValueFromCString('0')
    with open(PATH, 'a') as f:
        f.write(json.dumps(row) + '\n')
    print('CLOCK_NO_GLASS ' + json.dumps(row), flush=True)
    return False


def install_no_glass(debugger, slide, path):
    global SLIDE, PATH
    SLIDE, PATH = int(slide), str(path)
    names.SLIDE[0] = SLIDE
    bp = debugger.GetSelectedTarget().BreakpointCreateByAddress(0x1b38d67f4 + SLIDE)
    bp.SetScriptCallbackFunction(__name__ + '.no_glass')
