#!/usr/bin/env python3
"""Drive and verify the native UART input transport (DARWIN_INPUT_UART=1).

Host events enter QEMU through QMP ``input-send-event`` (the same absolute
pointer path the Cocoa window uses), QEMU's darwin-input queue forwards them
to the guest helper, and this tool reads QEMU's status file to separate the
stages: queued -> sent -> acknowledged by the helper -> submitted to the HID
system -> first presented frame.  Screendumps before/after are the only
witness for "the UI did something"; an ACK never is.

Coordinates are QEMU's normalised 0..32767 range.  ``--px`` accepts
framebuffer pixels instead (1179x2556 portrait); the guest reports the
digitizer position as a 0..1 fraction of the display, so pixel (x, y) maps to
round(x * 32767 / 1179), round(y * 32767 / 2556).  Backboardd then scales the
fraction to the 1179x2556 display; UIKit points are half of that.
"""
import argparse
import hashlib
import json
import socket
import re
from types import SimpleNamespace
import sys
import time
from pathlib import Path

FB_W, FB_H = 1179, 2556


class QMP:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(10)
        self.sock.connect(str(path))
        self.buf = b''
        self.recv()                                   # greeting
        self.execute('qmp_capabilities')

    def recv(self):
        while b'\n' not in self.buf:
            data = self.sock.recv(65536)
            if not data:
                raise RuntimeError('QMP closed')
            self.buf += data
        line, self.buf = self.buf.split(b'\n', 1)
        return json.loads(line)

    def execute(self, cmd, **args):
        self.sock.sendall(json.dumps(dict(execute=cmd, arguments=args)).encode() + b'\n')
        while True:
            reply = self.recv()
            if 'event' in reply:
                continue
            if 'error' in reply:
                raise RuntimeError(reply['error'])
            return reply.get('return')


class HMP:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(15)
        self.sock.connect(str(path))
        self.read()

    def read(self):
        buf = b''
        while not buf.rstrip().endswith(b'(qemu)'):
            chunk = self.sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        return buf.decode(errors='replace')

    def command(self, text):
        self.sock.sendall(text.encode() + b'\n')
        return self.read()


def norm(value, size, px):
    result = round(int(value) * 32767 / size) if px else int(value)
    if not 0 <= result <= 32767:
        raise ValueError('coordinate outside the framebuffer')
    return result


def read_status(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def wait_status(path, predicate, timeout, what):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = read_status(path)
        if last and predicate(last):
            return last
        time.sleep(0.05)
    raise TimeoutError(f'{what} not observed within {timeout}s; last status: {last}')


def idle(status):
    return (status['inflight'] == 0 and status['queue_len'] == 0 and
            status.get('wire_pending', 0) == 0 and status.get('wheel_pending', 0) == 0)


def ready(args, path):
    return wait_status(path, lambda s: s['guest_state'] == 'R' and idle(s) and
                       not s['contact_sent'], args.ready_timeout, 'idle guest helper')


def completed(args, path, before, minimum):
    def done(s):
        if s['epoch'] != before['epoch']:
            raise RuntimeError('input epoch changed during the operation; retry after recovery')
        sent = s['sent'] - before['sent'] - (s['pings'] - before['pings'])
        dispatched = (s['dispatched'] + s['dispatch_failed'] -
                      before['dispatched'] - before['dispatch_failed'])
        rejected = sum(s[k] - before[k] for k in ('ack_failed', 'ack_not_ready', 'ack_rejected'))
        return (idle(s) and sent >= minimum and dispatched + rejected >= sent and
                not s['contact_sent'])
    return wait_status(path, done, args.ack_timeout, 'HID operation completion')


def outcome(before, after):
    keys = ('sent', 'acked', 'ack_failed', 'ack_not_ready', 'ack_rejected',
            'timeouts', 'dispatched', 'dispatch_failed', 'overflow_or_not_ready_drops')
    result = {k: after[k] - before[k] for k in keys}
    result['records_sent'] = result.pop('sent')
    result['records_acked'] = result.pop('acked')
    return result


def successful(result):
    return (result['records_acked'] >= result['records_sent'] and
            not any(result[k] for k in ('ack_failed', 'ack_not_ready', 'ack_rejected',
                                       'timeouts', 'dispatch_failed', 'overflow_or_not_ready_drops')))


def send(qmp, x, y, down):
    qmp.execute('input-send-event', events=[
        dict(type='abs', data=dict(axis='x', value=x)),
        dict(type='abs', data=dict(axis='y', value=y)),
        dict(type='btn', data=dict(button='left', down=down)),
    ])


def move(qmp, x, y):
    qmp.execute('input-send-event', events=[
        dict(type='abs', data=dict(axis='x', value=x)),
        dict(type='abs', data=dict(axis='y', value=y)),
    ])


class DisplayPower:
    """Observe the guest's A484 power contract, independently of HID ACKs.

    Read incrementally: re-reading a multi-megabyte compositor log per poll
    would perturb the very interaction this tool is measuring.
    """
    def __init__(self, run):
        self.path = Path(run) / 'stderr.log'
        self.offset = 0
        self.partial = b''
        self.on = None

    def poll(self):
        with self.path.open('rb') as stream:
            if stream.seek(0, 2) < self.offset:
                raise RuntimeError('display log was replaced/truncated')
            stream.seek(self.offset)
            data = self.partial + stream.read()
            self.offset = stream.tell()
        lines = data.split(b'\n')
        self.partial = lines.pop()
        for line in lines:
            match = re.search(rb'iomfb: A484 display power \d+ -> (\d+) ', line)
            if match:
                value = int(match[1])
                if value not in (0, 1):
                    raise RuntimeError(f'unsupported display power state {value}')
                self.on = bool(value)
        return self.on


def wake_display(args, run):
    """Wake once if off; do not mistake that wake press for Home/unlock.

    No UI action is retried and no active contact is replayed. A484 ON is a
    power acknowledgement, not evidence of unlock or a completed animation.
    """
    power = DisplayPower(run)
    initial = power.poll()
    if initial is None:
        raise RuntimeError('display power unknown: no A484 evidence; refusing a blind wake toggle')
    if initial:
        return dict(ok=True, already_on=True, display_power_on=True)
    options = SimpleNamespace(**vars(args))
    options.frames = None
    result = key_press(options, run, 'f5', args.hold_ms)
    if not successful(result):
        raise RuntimeError(f'wake HID dispatch failed: {result}')
    identity = ready(args, Path(run) / 'input-status.json')
    deadline = time.monotonic() + args.wake_timeout
    while time.monotonic() < deadline:
        status = read_status(Path(run) / 'input-status.json')
        if not status or status['epoch'] != identity['epoch'] or status['guest_state'] != 'R':
            raise RuntimeError('input service changed while waiting for display wake')
        if power.poll():
            return dict(ok=True, already_on=False, display_power_on=True, input=result)
        time.sleep(.05)
    raise TimeoutError('Home dispatched but A484 display ON was not observed; no action replayed')


def screendump(hmp, path):
    hmp.command(f'screendump {json.dumps(str(path))} -f png')
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def key_press(args, run, qcode, hold_ms, button=None):
    """Home through the F5 key (qcode) or through the right mouse button."""
    status_path = run / 'input-status.json'
    before = ready(args, status_path)
    qmp = QMP(run / 'qmp.sock')
    hmp = HMP(run / 'monitor.sock')
    frames = {}
    if args.frames:
        frames['before'] = screendump(hmp, args.frames + '-before.png')
    t0 = time.monotonic()
    def key_event(down):
        if button:
            event = dict(type='btn', data=dict(button=button, down=down))
        else:
            event = dict(type='key', data=dict(down=down, key=dict(type='qcode', data=qcode)))
        qmp.execute('input-send-event', events=[event])
    try:
        key_event(True)
        time.sleep(hold_ms / 1000)
    finally:
        key_event(False)
    after = completed(args, status_path, before, 2)
    result = outcome(before, after)
    result.update(stage_host_to_dispatch_complete_ms=round((time.monotonic() - t0) * 1000, 1),
                  dispatch_last_us=after['dispatch_last_us'], guest_state=after['guest_state'])
    if args.frames:
        time.sleep(args.settle)
        frames['after'] = screendump(hmp, args.frames + '-after.png')
        result['frame_changed'] = frames['before'] != frames['after']
        result['frames'] = {k: dict(sha256=v) for k, v in frames.items()}
    return result


def wheel(args, run, x, y, notches):
    """Mouse wheel at (x, y): QEMU batches the notches into one W record and
    the guest performs a drag that ends at rest."""
    if not 1 <= abs(notches) <= 8:
        raise ValueError('wheel batch must be between 1 and 8 notches')
    status_path = run / 'input-status.json'
    before = ready(args, status_path)
    qmp = QMP(run / 'qmp.sock')
    hmp = HMP(run / 'monitor.sock')
    frames = {}
    if args.frames:
        frames['before'] = screendump(hmp, args.frames + '-before.png')
    t0 = time.monotonic()
    move(qmp, x, y)
    name = 'wheel-up' if notches > 0 else 'wheel-down'
    for _ in range(abs(notches)):
        qmp.execute('input-send-event', events=[
            dict(type='btn', data=dict(button=name, down=True)),
            dict(type='btn', data=dict(button=name, down=False))])
    after = completed(args, status_path, before, 1)
    result = outcome(before, after)
    result.update(stage_host_to_dispatch_complete_ms=round((time.monotonic() - t0) * 1000, 1),
                  dispatch_last_us=after['dispatch_last_us'], contact_sent=after['contact_sent'])
    if args.frames:
        time.sleep(args.settle)
        frames['after'] = screendump(hmp, args.frames + '-after.png')
        result['frame_changed'] = frames['before'] != frames['after']
        result['frames'] = {k: dict(sha256=v) for k, v in frames.items()}
    return result


def gesture(args, run, points, hold_ms):
    """Down at points[0], moves through the rest, up at the last point."""
    status_path = run / 'input-status.json'
    before = ready(args, status_path)
    qmp = QMP(run / 'qmp.sock')
    hmp = HMP(run / 'monitor.sock')
    frames = {}
    if args.frames:
        frames['before'] = screendump(hmp, args.frames + '-before.png')
    t0 = time.monotonic()
    try:
        send(qmp, points[0][0], points[0][1], True)
        step = hold_ms / max(1, len(points) - 1) / 1000
        for x, y in points[1:]:
            time.sleep(step)
            move(qmp, x, y)
        if len(points) == 1:
            time.sleep(hold_ms / 1000)
    finally:
        send(qmp, points[-1][0], points[-1][1], False)
    sent_at = time.monotonic()
    after = completed(args, status_path, before, 2)
    result = outcome(before, after)
    result.update(
        stage_host_to_dispatch_complete_ms=round((time.monotonic() - t0) * 1000, 1),
        dispatch_last_us=after['dispatch_last_us'], dispatch_max_us=after['dispatch_max_us'],
        coalesced=after['coalesced'] - before['coalesced'],
        guest_state=after['guest_state'], epoch=after['epoch'],
    )
    if args.frame_wait:
        try:
            probed = wait_status(status_path, lambda s: s['probe_open'] is False and
                                 s['input_to_present_samples'] > before['input_to_present_samples'],
                                 args.frame_wait, 'presented frame after input')
            result['input_to_present_ms'] = probed['input_to_present_ms']
        except TimeoutError as error:
            result['input_to_present_ms'] = None
            result['present_note'] = str(error).split(';')[0]
        time.sleep(args.settle)
    if args.frames:
        frames['after'] = screendump(hmp, args.frames + '-after.png')
        result['frame_changed'] = frames['before'] != frames['after']
        result['frames'] = {k: dict(sha256=v) for k, v in frames.items()}
    result['host_release_to_report_ms'] = round((time.monotonic() - sent_at) * 1000, 1)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--run', type=Path, required=True,
                   help='run directory holding qmp.sock, monitor.sock and input-status.json')
    p.add_argument('--wake', action='store_true', help='wake an off display and await A484 ON before this action')
    p.add_argument('--wake-timeout', type=float, default=10)
    p.add_argument('--px', action='store_true', help='coordinates are framebuffer pixels')
    p.add_argument('--hold-ms', type=int, default=80, help='tap hold / swipe duration')
    p.add_argument('--steps', type=int, default=12, help='swipe motion samples')
    p.add_argument('--frames', help='prefix for before/after screendumps')
    p.add_argument('--frame-wait', type=float, default=5, help='seconds to wait for a presented frame')
    p.add_argument('--settle', type=float, default=1.5, help='seconds before the after-frame')
    p.add_argument('--ready-timeout', type=float, default=5)
    p.add_argument('--ack-timeout', type=float, default=40)
    p.add_argument('--log', type=Path, help='append the JSON result here')
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('tap'); s.add_argument('x'); s.add_argument('y')
    s = sub.add_parser('swipe'); [s.add_argument(n) for n in ('x1', 'y1', 'x2', 'y2')]
    s = sub.add_parser('home'); s.add_argument('--button', choices=('right',),
                                                help='use the right mouse button instead of F5')
    sub.add_parser('power', help='press F6 (Power)')
    s = sub.add_parser('wheel'); s.add_argument('x'); s.add_argument('y')
    s.add_argument('notches', type=int, help='positive = wheel up (content scrolls up)')
    sub.add_parser('wake', help='idempotent display wake; does not claim unlock')
    sub.add_parser('status')
    s = sub.add_parser('wait-ready'); s.add_argument('--timeout', type=float, default=600)
    s = sub.add_parser('frame'); s.add_argument('path')
    args = p.parse_args()
    if args.hold_ms < 0 or args.settle < 0:
        p.error('hold and settle durations must be nonnegative')
    run = args.run
    status_path = run / 'input-status.json'
    if args.cmd == 'status':
        print(json.dumps(read_status(status_path), indent=1))
        return
    if args.cmd == 'wait-ready':
        status = wait_status(status_path, lambda s: s['guest_state'] == 'R', args.timeout, 'readiness')
        print(json.dumps(dict(ready=True, guest_pid=status['guest_pid'], epoch=status['epoch'])))
        return
    if args.cmd == 'frame':
        print(json.dumps(dict(path=args.path, sha256=screendump(HMP(run / 'monitor.sock'), args.path))))
        return
    if args.wake and args.cmd not in ('home', 'tap', 'swipe', 'wheel'):
        p.error('--wake applies to home, tap, swipe or wheel')
    if args.cmd == 'wake' or args.wake:
        try:
            wake = wake_display(args, run)
        except (TimeoutError, RuntimeError, OSError, ValueError) as error:
            print(json.dumps(dict(command=args.cmd, ok=False, error=str(error))))
            sys.exit(1)
        if args.cmd == 'wake':
            print(json.dumps(wake, indent=1))
            return
    else:
        wake = None
    if args.cmd in ('home', 'power'):
        result = dict(command=args.cmd, run=str(run), unix=time.time(), wake=wake)
        try:
            result.update(key_press(args, run, 'f5' if args.cmd == 'home' else 'f6',
                                    args.hold_ms, button=getattr(args, 'button', None)))
            result['ok'] = successful(result)
        except (TimeoutError, RuntimeError, OSError, ValueError) as error:
            result.update(ok=False, error=str(error))
        print(json.dumps(result, indent=1))
        if args.log:
            with args.log.open('a') as log:
                log.write(json.dumps(result) + '\n')
        sys.exit(0 if result.get('ok') else 1)
    if args.cmd == 'wheel':
        result = dict(command='wheel', notches=args.notches, run=str(run), unix=time.time(), wake=wake)
        try:
            result.update(wheel(args, run, norm(args.x, FB_W, args.px), norm(args.y, FB_H, args.px),
                                args.notches))
            result['ok'] = successful(result)
        except (TimeoutError, RuntimeError, OSError, ValueError) as error:
            result.update(ok=False, error=str(error))
        print(json.dumps(result, indent=1))
        if args.log:
            with args.log.open('a') as log:
                log.write(json.dumps(result) + '\n')
        sys.exit(0 if result.get('ok') else 1)
    if args.cmd == 'tap':
        points = [(norm(args.x, FB_W, args.px), norm(args.y, FB_H, args.px))]
    else:
        x1, y1 = norm(args.x1, FB_W, args.px), norm(args.y1, FB_H, args.px)
        x2, y2 = norm(args.x2, FB_W, args.px), norm(args.y2, FB_H, args.px)
        n = max(2, args.steps)
        points = [(round(x1 + (x2 - x1) * i / (n - 1)), round(y1 + (y2 - y1) * i / (n - 1)))
                  for i in range(n)]
    result = dict(command=args.cmd, points=points, hold_ms=args.hold_ms,
                  run=str(run), unix=time.time(), wake=wake)
    try:
        result.update(gesture(args, run, points, args.hold_ms))
        # Pings sent between the gesture's records are acknowledged too.
        result['ok'] = successful(result)
    except (TimeoutError, RuntimeError, OSError, ValueError) as error:
        result.update(ok=False, error=str(error))
    print(json.dumps(result, indent=1))
    if args.log:
        with args.log.open('a') as log:
            log.write(json.dumps(result) + '\n')
    sys.exit(0 if result.get('ok') else 1)


if __name__ == '__main__':
    main()
