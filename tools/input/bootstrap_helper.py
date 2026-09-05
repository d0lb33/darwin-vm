"""One-time native posix_spawn bootstrap for an already trusted input helper.

Run in the 24A5430a guest while starting from a checkpoint. The helper runs
independently after this callback deletes its own breakpoint. No executable
guest memory is written; mmap holds only spawn strings and file actions.
Clone launchd's registered bootstrap send right through native Mach APIs and
pass it as TASK_BOOTSTRAP_PORT in native spawn attributes. Do not spawn from
sandboxed daemons: notifyd terminates with OS_REASON_SANDBOX on this image.
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
PROCESS = 'launchd'
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
    def self_port(frame):
        return calls.u64(frame.GetThread().GetProcess(), 0x26fd74078 + calls.SLIDE) & 0xffffffff
    def checked(address, arguments):
        def step(frame, state):
            if state['results'][-1]:
                raise RuntimeError('native setup failed at stage %d: %#x' %
                                   (state['index']-1, state['results'][-1]))
            return address, arguments(frame, state)
        return step
    def bootstrap(frame, state):
        if state['results'][-1]:
            raise RuntimeError('registered-port lookup failed')
        port = calls.u64(frame.GetThread().GetProcess(), base(state)+0x1100) & 0xffffffff
        if not port or port == 0xffffffff:
            raise RuntimeError('launchd has no registered bootstrap port')
        state['bootstrap_port'] = port
        print('NATIVE_INPUT_BOOTSTRAP_PORT name=%#x' % port, flush=True)
        return 0x237cd5cac, dict(x0=self_port(frame),x1=port,x2=base(state)+0x1110)
    def attributes(frame, state):
        kind = calls.u64(frame.GetThread().GetProcess(), base(state)+0x1110) & 0xffffffff
        if state['results'][-1] or not kind & 0x10000:
            raise RuntimeError('registered bootstrap port lacks a send right')
        return 0x237cdc9b0, dict(x0=base(state)+0x1200)
    def release_port(index):
        def step(frame, state):
            port = int.from_bytes(calls.read(frame.GetThread().GetProcess(),
                                            base(state)+0x1100+index*4,4),'little')
            # getpid is a harmless placeholder for an empty registered slot.
            return (0x237cd3d24, dict(x0=self_port(frame),x1=port)) if port else (0x237cd515c,{})
        return step
    def addopen(fd):
        return checked(0x237cda3e8,lambda f,s:dict(x0=base(s)+0x1000,x1=fd,x2=base(s)+0x900,x3=1,x4=0))
    def spawned(frame, state):
        result = state['results'][-1]
        pid = calls.u64(frame.GetThread().GetProcess(), base(state)) & 0xffffffff
        print('NATIVE_INPUT_SPAWN_RESULT error=%d pid=%d' % (result, pid), flush=True)
        return 0x237cdb748, dict(x0=base(state)+0x1000)
    return [(0x237cd5d8c,dict(x0=0,x1=0x4000,x2=3,x3=0x1002,x4=0xffffffffffffffff,x5=0)),
            lambda f,s:(0x18c3738e0,dict(x0=base(s),x1=0x4000)),
            lambda f,s:(0x237cdb474,dict(x0=base(s)+0x1000)),
            prepare, addopen(1), addopen(2),
            checked(0x237cfb494,lambda f,s:dict(x0=self_port(f),x1=base(s)+0x1100,
                                              x2=base(s)+0x1104,x3=base(s)+0x1108)),
            bootstrap, attributes,
            checked(0x237cdb870,lambda f,s:dict(x0=base(s)+0x1200,x1=s['bootstrap_port'],x2=4)),
            checked(0x237cdbeb0,lambda f,s:dict(x0=base(s),x1=base(s)+0x400,x2=base(s)+0x1000,
                                               x3=base(s)+0x1200,x4=base(s)+0x100,x5=base(s)+0x200)),
            spawned,
            lambda f,s:(0x237cdedd8,dict(x0=base(s)+0x1200)),
            release_port(0),release_port(1),release_port(2),
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
        if frame.FindRegister('cpsr').GetValueAsUnsigned() & 15:
            raise RuntimeError('spawn requires an EL0 call boundary')
        STARTED = True
        print('NATIVE_INPUT_SPAWN_START process=%s tp=%#x' %
              (PROCESS,frame.FindRegister('tpidr_el1').GetValueAsUnsigned()),flush=True)
        calls.begin(frame,steps(),'native-input-spawn')
        return False
    except Exception as error:
        print('NATIVE_INPUT_SPAWN_ERROR',str(error),flush=True)
        return True


def install(debugger, slide, command='helper', process='launchd'):
    global COMMAND, PROCESS, STARTED
    if command not in ('helper','bootstrap'):
        raise ValueError('unsupported bootstrap command')
    if process != 'launchd':
        raise ValueError('only launchd is supported; notifyd spawning triggers SANDBOX termination')
    COMMAND = command
    PROCESS = process
    STARTED = False
    calls.SLIDE = names.SLIDE[0] = slide
    if calls.STATE:
        raise RuntimeError('another native call is active')
    # Verified launchd sendto return, after its native thread initialization.
    # launchd's own bootstrap port is null; clone its registered root port
    # through Mach and pass TASK_BOOTSTRAP_PORT in native spawn attributes.
    bp = debugger.GetSelectedTarget().BreakpointCreateByAddress(0x237cd6214+slide)
    error = bp.SetScriptCallbackFunction(__name__+'.callback')
    if error is not None and error.Fail():
        debugger.GetSelectedTarget().BreakpointDelete(bp.GetID())
        raise RuntimeError(str(error))
    print('NATIVE_INPUT_SPAWN_ARMED breakpoint=%d'%bp.GetID(),flush=True)
