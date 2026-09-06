"""Opt-in 24A5430a UI startup diagnostic using the existing ringer cache.

SBAVSystemControllerCache isRingerMuted (0x22463c690) synchronously enters its
audio queue; its block at 0x22463c73c..748 only copies the receiver byte +0x61.
NATIVE_SETUP_UART_R1's main thread waits for that queue during ADFL while
FigRoutingContextResilientRemoteCopySystemMusicContext waits for an XPC reply.
This diagnostic reads that same cached byte without waiting for queue ownership.
It does not emulate audio, alter the cache, or assume a mute value.
"""
import welcome_abort_callbacks as names


def hit(frame, location, _dict):
    process = frame.GetThread().GetProcess()
    if names._progname(process) != 'SpringBoard':
        return False
    obj = frame.FindRegister('x0').GetValueAsUnsigned()
    value = names._read(process, obj + 0x61, 1)
    lr = frame.FindRegister('lr').GetValueAsUnsigned() & 0xffffffffffff
    if value not in (b'\0', b'\1') or not lr:
        print('CACHED_AUDIO_INVALID_RECEIVER', hex(obj), flush=True)
        return True
    if not frame.FindRegister('x0').SetValueFromCString(str(value[0])):
        return True
    if not frame.FindRegister('pc').SetValueFromCString(hex(lr)):
        return True
    print('CACHED_AUDIO_RINGER receiver=%#x cached=%d' % (obj, value[0]), flush=True)
    return False


def install(debugger, slide):
    names.SLIDE[0] = slide
    bp = debugger.GetSelectedTarget().BreakpointCreateByAddress(0x22463c690 + slide)
    bp.SetScriptCallbackFunction(__name__ + '.hit')
    print('CACHED_AUDIO_ARMED', bp.GetID(), flush=True)
