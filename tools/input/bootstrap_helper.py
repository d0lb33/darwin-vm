"""One-time native posix_spawn bootstrap for an already trusted input helper.

Run in the 24A5430a guest while starting from a checkpoint. The helper runs
independently after this callback deletes its own breakpoint. No executable
guest memory is written; mmap holds only spawn strings and file actions.
"""
import struct
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'re'))
import lldb
import touch_bridge as calls
import welcome_abort_callbacks as names

STARTED = False
COMMAND = 'helper'
PROCESS = 'notifyd'
SEEN = set()


def steps():
    def base(state):
        address = state['results'][0]
        if not 0x1000 <= address < 1 << 43:
            raise RuntimeError('native mmap failed: '+hex(address))
        return address
    def prepare(frame, state):
        address = base(state)
        if state['results'][2] != 0:
            raise RuntimeError('file-actions init failed')
        data = bytearray(0x1000)
        # Keep the native file-actions pointer initialized in the second page.
        program = b'/bin/launchctl' if COMMAND == 'bootstrap' else b'/usr/local/libexec/dvm-input'
        strings = [program]
        if COMMAND == 'bootstrap':
            strings += [b'bootstrap', b'system', b'/System/Library/LaunchDaemons/com.apple.dvm-input.plist']
        for index, string in enumerate(strings):
            offset = 0x400+index*0x100
            data[offset:offset+len(string)+1] = string+b'\0'
            struct.pack_into('<Q', data, 0x100+8*index, address+offset)
        data[0x900:0x90d] = b'/dev/console\0'
        error = lldb.SBError()
        if frame.GetThread().GetProcess().WriteMemory(address, bytes(data), error) != len(data) or error.Fail():
            raise RuntimeError('cannot write spawn data')
        return 0x237cda3e8, dict(x0=address+0x1000,x1=0,x2=address+0x900,x3=0,x4=0)
    def addopen(fd):
        return lambda f,s: (0x237cda3e8,dict(x0=base(s)+0x1000,x1=fd,x2=base(s)+0x900,x3=1,x4=0))
    return [(0x237cd5d8c,dict(x0=0,x1=0x4000,x2=3,x3=0x1002,x4=0xffffffffffffffff,x5=0)),
            lambda f,s:(0x18c3738e0,dict(x0=base(s),x1=0x4000)),
            lambda f,s:(0x237cdb474,dict(x0=base(s)+0x1000)),
            prepare, addopen(1), addopen(2),
            lambda f,s:(0x237cdbeb0,dict(x0=base(s),x1=base(s)+0x400,x2=base(s)+0x1000,
                                       x3=0,x4=base(s)+0x100,x5=base(s)+0x200)),
            lambda f,s:(0x237cdb748,dict(x0=base(s)+0x1000)),
            lambda f,s:(0x237cd6344,dict(x0=base(s),x1=0x4000))]


def callback(frame, location, _dict):
    global STARTED
    try:
        if calls.STATE:
            state = calls.STATE
            if (frame.FindRegister('tpidr_el1').GetValueAsUnsigned() != state['tp'] or
                    frame.FindRegister('sp').GetValueAsUnsigned() != state['sp']):
                return False
            state['results'].append(frame.FindRegister('x0').GetValueAsUnsigned())
            state['index'] += 1
            if state['index'] < len(state['steps']):
                calls.advance(frame)
                return False
            for name, raw in state['regs'].items():
                error = lldb.SBError(); data = lldb.SBData()
                data.SetData(error, raw, lldb.eByteOrderLittle, 8)
                if not frame.FindRegister(name).SetData(data,error) or error.Fail():
                    raise RuntimeError('register restore: '+name)
            print('NATIVE_INPUT_SPAWN_DONE results='+repr([hex(v) for v in state['results']]),flush=True)
            calls.STATE = None
            location.GetBreakpoint().SetEnabled(False)
            return False
        name = names._progname(frame.GetThread().GetProcess())
        if name not in SEEN and len(SEEN) < 24:
            SEEN.add(name)
            print('NATIVE_INPUT_BOOTSTRAP_SEEN '+name,flush=True)
        if STARTED or name != PROCESS:
            return False
        STARTED = True
        print('NATIVE_INPUT_SPAWN_START process=%s tp=%#x' %
              (PROCESS,frame.FindRegister('tpidr_el1').GetValueAsUnsigned()),flush=True)
        calls.begin(frame,steps(),'native-input-spawn')
        return False
    except Exception as error:
        print('NATIVE_INPUT_SPAWN_ERROR',str(error),flush=True)
        return True


def install(debugger, slide, command='helper', process='notifyd'):
    global COMMAND, PROCESS, STARTED
    if command not in ('helper','bootstrap'):
        raise ValueError('unsupported bootstrap command')
    COMMAND = command
    PROCESS = process
    STARTED = False
    calls.SLIDE = names.SLIDE[0] = slide
    if calls.STATE:
        raise RuntimeError('another native call is active')
    # A dispatch-based daemon need not use CF observers or mach_msg's older
    # wrapper. The mach_msg2_trap return is live in its worker threads; these
    # libc/syscall-only calls; preserve its returned registers and flags.
    bp = debugger.GetSelectedTarget().BreakpointCreateByAddress(0x237ccfcd4+slide)
    bp.SetScriptCallbackFunction(__name__+'.callback')
    print('NATIVE_INPUT_SPAWN_ARMED breakpoint=%d'%bp.GetID(),flush=True)
