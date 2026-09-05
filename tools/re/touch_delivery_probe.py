"""Bounded native Recap delivery trace for 24A5430a touch bring-up."""
import welcome_abort_callbacks as names

POINTS = {
    0x29b20553c:'inline-actions', 0x29b1fe1b0:'inline-stream',
    0x29b1fc2e8:'player-stream', 0x29b1fc1e8:'delivery-service',
    0x29b1fe5c4:'virtual-hid-init', 0x29b1fc950:'send-event',
    0x29b1fc2c0:'delivery-result',
    0x2247de6a8:'tap-block', 0x2247de724:'tap-composer',
    0x29b200014:'post-result', 0x2678a7220:'dispatch-result',
    0x22439e9fc:'springboard-hid',
    0x22a352848:'bk-system', 0x22a351fac:'bk-primary',
    0x22a415e40:'bk-direct', 0x22a415e88:'bk-sender-result',
    0x22a414320:'bk-touch', 0x22a416dd8:'bk-display',
    0x22a416e20:'bk-state', 0x22a416e80:'bk-ignore',
    0x22a416f98:'bk-path',
    0x22a414e48:'bk-hit-coordinates', 0x22a414e4c:'bk-hit-result',
    0x22a35623c:'bk-client-dispatch-result',
    0x184e3619c:'ui-fetch', 0x184e36a98:'ui-transform',
    0x18493425c:'ui-event', 0x184e40dc0:'ui-window-hit',
    0x224325968:'sb-send', 0x1c4b0c820:'icon-down',
    0x1c4b10cf4:'icon-up', 0x1c4b0ec04:'icon-tap',
}
COUNTS = {}
SLIDE = 0x14f94000

def hit(frame, loc, _dict):
    pc = loc.GetAddress().GetLoadAddress(frame.GetThread().GetProcess().GetTarget())-SLIDE
    COUNTS[pc] = COUNTS.get(pc,0)+1
    if COUNTS[pc] <= 8:
        print('TOUCH_DELIVERY', POINTS.get(pc,hex(pc)),
              names._progname(frame.GetThread().GetProcess()),
              {r:frame.FindRegister(r).GetValue() for r in ('x0','x2','x3','d0','d1')},flush=True)
    if COUNTS[pc] >= 8:
        loc.GetBreakpoint().SetEnabled(False)
    return False

def install(debugger):
    for a in POINTS:
        b=debugger.GetSelectedTarget().BreakpointCreateByAddress(a+SLIDE)
        b.SetScriptCallbackFunction('touch_delivery_probe.hit')
