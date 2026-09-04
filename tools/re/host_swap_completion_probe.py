"""One-shot host LLDB experiment for the 24A5430a D594 callback.

The parent must independently verify the pending AP swap ID before install.
At bootkc a0db034 the wire payload is 0x730 bytes: ID at 0, cancellation
Boolean at 4, eight optional 0xe0 timing records at 0x27, count at 0x727,
and null swap-info at 0x72c. a0c58bc finds that ID in fb+0x4138, completes
its surfaces and removes it. This probe supplies no invented timing data.
It appends to a completed script under BQL, using the existing transport.
Never install on an arbitrary or unverified pending swap.
"""
import json
import time

import lldb

SWAP_ID = None
MODEL_ADDRESS = None
EVENT_PATH = '/tmp/dvm/host-swap-completion.jsonl'


def evaluate(frame, source):
    options = lldb.SBExpressionOptions()
    options.SetIgnoreBreakpoints(True)
    options.SetTimeoutInMicroSeconds(3000000)
    value = frame.EvaluateExpression(source, options)
    if value.GetError().Fail():
        raise RuntimeError(value.GetError().GetCString())
    return value.GetValueAsUnsigned()


def on_message(frame, location, _dict):
    location.GetBreakpoint().SetEnabled(False)
    event = dict(time=time.time(), swap_id=SWAP_ID)
    try:
        m = MODEL_ADDRESS or frame.FindVariable('m').GetValueAsUnsigned()
        ep = 0x37 if MODEL_ADDRESS else frame.FindVariable('ep').GetValueAsUnsigned()
        event.update(model=m, endpoint=ep)
        assert m and ep == 0x37
        assert evaluate(frame, 'bql_locked()'), 'BQL not owned'
        prefix = '(DarwinIOMFB *)0x%x' % m
        assert evaluate(frame, '({ DarwinIOMFB *s=%s; s->heap_known && '
                        's->level >= 3 && !s->cb_busy && s->cb_script && '
                        's->cb_next == s->cb_script->len; })' % prefix), 'script not idle'
        source = ('({ DarwinIOMFB *s=%s; IOMFBCallback cb={0}; '
                  'unsigned char in[0x730]={0}; '
                  '*(unsigned int *)in=%d; in[0x72c]=1; '
                  'cb.name[0]=68; cb.name[1]=53; cb.name[2]=57; cb.name[3]=52; '
                  'cb.name_be=0x44353934; cb.in=(GByteArray *)g_byte_array_new(); '
                  '(GByteArray *)g_byte_array_append(cb.in,in,sizeof(in)); '
                  '(GArray *)g_array_append_vals(s->cb_script,&cb,1); '
                  'iomfb_callback_pump(s,0x37); s->cb_busy; })' %
                  (prefix, SWAP_ID))
        assert evaluate(frame, source), 'callback not sent'
        event['action'] = 'sent-D594'
    except Exception as error:
        event.update(action='stop-error', error=str(error))
    with open(EVENT_PATH, 'a') as stream:
        stream.write(json.dumps(event, sort_keys=True) + '\n')
    print('HOST_SWAP_COMPLETION ' + json.dumps(event, sort_keys=True), flush=True)
    return event['action'] != 'sent-D594'


def install(debugger, verified_swap_id, event_path, model_address=None):
    global SWAP_ID, EVENT_PATH, MODEL_ADDRESS
    assert isinstance(verified_swap_id, int) and 0 <= verified_swap_id <= 0xffffffff
    SWAP_ID, EVENT_PATH = verified_swap_id, event_path
    MODEL_ADDRESS = model_address
    debugger.HandleCommand('process handle SIGUSR2 -s false -n false -p true')
    debugger.HandleCommand('process handle SIGUSR1 -s false -n false -p true')
    # Any ASC send owns the device lock, allowing completion even after the
    # AP has stopped issuing display RPCs. The caller supplies the previously
    # observed model pointer in that case; the BQL and idle checks still apply.
    bp = debugger.GetSelectedTarget().BreakpointCreateByName(
        'darwin_asc_send' if model_address else 'darwin_iomfb_handle')
    assert bp.GetNumLocations() == 1
    bp.SetScriptCallbackFunction(__name__ + '.on_message')
    print('HOST_SWAP_COMPLETION_READY id=%d swap=%d' % (bp.GetID(), SWAP_ID))
