"""Read BYSetupAssistantNeedsToRun()'s return value on a booted image.
w0 at SetupAssistant 0x1cac11cf0 (BY_NEEDS_COMMON_RETURN_RESULT): 1 = Setup
still needed, 0 = complete. Prints each caller's result; no state change."""
import lldb
import welcome_abort_callbacks as names
SLIDE=0
RET=0x1cac11cf0
HITS=[0]
def on_ret(frame,location,_d):
    p=frame.GetThread().GetProcess()
    w0=frame.FindRegister('x0').GetValueAsUnsigned() & 0xffffffff
    print('NEEDSRUN process=%s w0=%d' % (names._progname(p), w0), flush=True)
    HITS[0]+=1
    if HITS[0]>=8: location.GetBreakpoint().SetEnabled(False)
    return False
def install(debugger,slide):
    global SLIDE; SLIDE=slide; names.SLIDE[0]=slide
    t=debugger.GetSelectedTarget()
    bp=t.BreakpointCreateByAddress(RET+slide); bp.SetScriptCallbackFunction('verify_needsrun_callbacks.on_ret')
    print('NEEDSRUN_READY bp=%d at %#x'%(bp.GetID(),RET+slide),flush=True)
