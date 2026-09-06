"""Opt-in QEMU single-finger gesture bridge for the 24A5430a Home checkpoint.

Records QEMU down/move/up and replays the completed gesture through native
Recap. This first implementation replays after release, not during dragging.
No touch-controller register semantics are invented. See docs/re/single-touch.md.
"""
import json
import os
import struct
import time
from pathlib import Path

import lldb
import welcome_abort_callbacks as names

SLIDE = 0x14f94000
OBSERVER = 0x1806544e8
POLL_ENTRY = 0x2242eb168  # -[SpringBoard isShowingHomescreen], before PAC
STATE = None
FILE = None
PARTIAL = ''
POINTS = []
QUEUE = []
TIMER = False
LAST_RESET = 0
COUNTS = {'gestures': 0, 'calls': 0}


def read(p, a, n):
    e = lldb.SBError()
    b = p.ReadMemory(a, n, e)
    if e.Fail() or b is None or len(b) != n:
        raise RuntimeError('guest read failed at %#x: %s' % (a, e))
    return b


def u64(p, a):
    return int.from_bytes(read(p, a, 8), 'little')


def reg(frame, name, value):
    r = frame.FindRegister(name)
    e = lldb.SBError()
    raw = struct.pack('<d', value) if isinstance(value, float) else int(value).to_bytes(r.GetByteSize(), 'little')
    d = lldb.SBData()
    d.SetData(e, raw, lldb.eByteOrderLittle, 8)
    if e.Fail() or not r.SetData(d, e):
        raise RuntimeError('cannot write register ' + name)


def poll():
    global PARTIAL, POINTS
    PARTIAL += FILE.read()
    lines = PARTIAL.split('\n')
    PARTIAL = lines.pop()
    for line in lines:
        e = json.loads(line)
        if not (0 <= e['x'] <= 32767 and 0 <= e['y'] <= 32767):
            continue
        if e['down']:
            POINTS.append(e)
            if len(POINTS) > 128:
                POINTS = POINTS[::2]
        elif POINTS:
            POINTS.append(e)
            if len(QUEUE) < 8:
                QUEUE.append(POINTS)
            else:
                print('TOUCH queue full; gesture discarded', flush=True)
            POINTS = []


def begin(frame, steps, label):
    global STATE
    regs = {}
    for n in ([f'x{i}' for i in range(29)] + ['fp','lr','sp','pc','cpsr'] +
              [f'v{i}' for i in range(32)] + ['fpsr','fpcr']):
        r = frame.FindRegister(n)
        if r.IsValid():
            e = lldb.SBError()
            regs[n] = r.GetData().ReadRawData(e, 0, r.GetByteSize())
            if e.Fail():
                raise RuntimeError('cannot save ' + n)
    # SBFrame's cached PC can describe an older CPU stop after guest thread
    # migration. The raw register snapshot is authoritative (R20 recorded
    # GetPC=0x195614724 while the saved PC was the observer 0x1955e84e8).
    STATE = {'regs':regs, 'sp':int.from_bytes(regs['sp'],'little'),
             'pc':int.from_bytes(regs['pc'],'little'),
             'tp':frame.FindRegister('tpidr_el1').GetValueAsUnsigned(),
             'steps':steps, 'index':0, 'results':[], 'label':label}
    advance(frame)


def advance(frame):
    step = STATE['steps'][STATE['index']]
    address, args = step(frame, STATE) if callable(step) else step
    reg(frame, 'lr', STATE['pc'])
    reg(frame, 'x1', 0)
    for n, v in args.items():
        reg(frame, n, v)
    reg(frame, 'pc', address + SLIDE)
    COUNTS['calls'] += 1
    print('TOUCH_CALL %s stage=%d pc=%#x' % (STATE['label'], STATE['index'], address), flush=True)


def stream_step(address, extra=None):
    return lambda f,s: (address, {'x0':s['results'][0], **(extra or {})})


def playback_steps(options_index, actions=False):
    # BackBoard receives the virtual digitizer but has no physical touch
    # service from which to infer a display. Recap exposes an explicit display
    # UUID override; use the guest's BKSDisplayUUIDMainKey NSString.
    def set_display(f, s):
        p = f.GetThread().GetProcess()
        return 0x29b2056c0, {'x0':s['results'][options_index],
                            'x2':u64(p,0x1e080b360+SLIDE)}
    return [(0x180409c0c, {'x0':0x2ce3067d0+SLIDE}), set_display,
            lambda f,s:(0x29b2051c0 if actions else 0x29b1fcdd8,
                {'x0':0x2ce306668+SLIDE, 'x2':s['results'][0],
                 'x3':s['results'][options_index], 'x4':0}),
            lambda f,s:(0x180405964, {'x0':s['results'][options_index]})]


def gesture_steps(points):
    # Use Recap's actual screenSize, not an assumed iPhone points/scale.
    # R21 reports 1179x2556 with gsScreenScaleFactor=1. Sending 46,85
    # reached UIKit at 23,42.5 and missed the icon; the host position is
    # 138,255 in this stream's coordinate space. See single-touch.md.
    xy = [(p['x'] / 32767, p['y'] / 32767) for p in points]
    duration = max(.10, min(3.0, (points[-1]['t']-points[0]['t'])/1000))
    distance = max(abs(x-xy[0][0])+abs(y-xy[0][1]) for x,y in xy)
    steps = [(0x180409c0c, {'x0':0x2ce3065f0+SLIDE}),
             lambda f,s:(0x29b208234, {'x0':0x2ce306690+SLIDE,
                 'x2':u64(f.GetThread().GetProcess(),0x1e080b360+SLIDE)}),
             lambda f,s:(0x29b214560, {'x0':s['results'][0], 'x2':s['results'][1]})]
    def position_step(address, point, extra=None):
        def step(f,s):
            p = f.GetThread().GetProcess()
            offset = int.from_bytes(read(p,0x2cc7a7bfc+SLIDE,4),'little')
            width,height = struct.unpack('<dd',read(p,s['results'][0]+offset,16))
            if not (0 < width <= 16384 and 0 < height <= 16384):
                raise RuntimeError('invalid native Recap screen size')
            return address, {'x0':s['results'][0], 'd0':point[0]*width,
                             'd1':point[1]*height, **(extra or {})}
        return step
    if distance < 5 / 1179:
        steps.append(position_step(0x29b20041c, xy[0], {'x2':1,'x3':1}))
    else:
        steps.append(position_step(0x29b20069c, xy[0]))
        # Bounded interpolation through the actual captured path.
        count = min(12, len(xy)-1)
        for i in range(1,count+1):
            x,y = xy[round(i*(len(xy)-1)/count)]
            steps.append(position_step(0x29b2006fc, (x,y), {'d2':duration/count}))
        steps.append(position_step(0x29b20072c, xy[-1]))
    steps.append(stream_step(0x29b200040))
    def set_events(f,s):
        p = f.GetThread().GetProcess()
        offset = int.from_bytes(read(p,0x2cc7a7bec+SLIDE,4),'little')
        return 0x29b209230, {'x0':s['results'][0], 'x2':u64(p,s['results'][0]+offset)}
    steps += [set_events]
    steps += playback_steps(len(steps))
    steps += [stream_step(0x180405964)]
    return steps


def on_observer(frame, location, _dict):
    global STATE, TIMER, LAST_RESET
    try:
        p = frame.GetThread().GetProcess()
        tp = frame.FindRegister('tpidr_el1').GetValueAsUnsigned()
        if STATE:
            if tp != STATE['tp'] or frame.FindRegister('sp').GetValueAsUnsigned() != STATE['sp']:
                return False
            STATE['results'].append(frame.FindRegister('x0').GetValueAsUnsigned())
            STATE['index'] += 1
            if STATE['index'] < len(STATE['steps']):
                advance(frame)
                return False
            label, results = STATE['label'], STATE['results']
            for n,b in STATE['regs'].items():
                e = lldb.SBError(); d = lldb.SBData()
                d.SetData(e,b,lldb.eByteOrderLittle,8)
                if e.Fail() or not frame.FindRegister(n).SetData(d,e):
                    raise RuntimeError('restore failed: '+n)
            STATE = None
            print('TOUCH_DONE %s results=%s' % (label, [hex(v) for v in results]), flush=True)
            if label == 'timer':
                TIMER = True
                # The common CF observer runs in every process. Once our
                # NSTimer exists, move to its SpringBoard-only selector so
                # unrelated run loops no longer stop all virtual CPUs.
                target = p.GetTarget()
                bp = target.BreakpointCreateByAddress(POLL_ENTRY+SLIDE)
                bp.SetScriptCallbackFunction('touch_bridge.on_observer')
                location.GetBreakpoint().SetEnabled(False)
                print('TOUCH_POLL_READY breakpoint=%d' % bp.GetID(),flush=True)
            elif label.startswith('gesture'):
                COUNTS['gestures'] += 1
            return False
        if not 0x160000000 <= frame.FindRegister('sp').GetValueAsUnsigned() < 0x180000000 or names._progname(p) != 'SpringBoard':
            return False
        sb = u64(p,0x270951218+SLIDE)
        if not TIMER:
            # Periodic native main-run-loop wake; harmless read-only selector.
            begin(frame,[(0x180cee2a8, {'x0':0x1e6f3d0e8+SLIDE, 'x2':sb,
                  'x3':0x1f65749d7+SLIDE,'x4':0,'x5':1,'d0':.2})], 'timer')
            return False
        poll()
        if QUEUE:
            points = QUEUE.pop(0)
            begin(frame, gesture_steps(points), 'gesture-%d' % (COUNTS['gestures']+1))
        elif time.monotonic()-LAST_RESET > 5:
            offset = int.from_bytes(read(p,0x26cf35748+SLIDE,4),'little')
            idle = u64(p,sb+offset)
            begin(frame, [(0x2242fa85c,{'x0':idle,'x2':0x2732af6a8+SLIDE})], 'idle-reset')
            LAST_RESET = time.monotonic()
        return False
    except Exception as e:
        print('TOUCH_ERROR', str(e), flush=True)
        return True


def install(debugger, path, slide=0x14f94000):
    global FILE, SLIDE
    SLIDE = slide
    names.SLIDE[0] = slide
    Path(path).touch(mode=0o600, exist_ok=True)
    FILE = open(path)
    b = debugger.GetSelectedTarget().BreakpointCreateByAddress(OBSERVER+slide)
    error = b.SetScriptCallbackFunction('touch_bridge.on_observer')
    print('TOUCH_READY file=%s breakpoint=%d' % (path,b.GetID()), flush=True)
