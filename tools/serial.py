#!/usr/bin/env python3
"""serial.py - drive a guest serial console over a QEMU unix socket.

Boot qemu with a socket-backed serial port instead of a file or stdio:

    -serial unix:/tmp/dvm/sh.sock,server,nowait

then talk to it from a script:

    tools/serial.py /tmp/dvm/sh.sock read --secs 5      # drain what is there
    tools/serial.py /tmp/dvm/sh.sock send 'mount -uw /' # send a line, show reply
    tools/serial.py /tmp/dvm/sh.sock script cmds.txt    # one command per line

Everything the guest sends is appended to a log (--log, default alongside the
socket) so a long boot can be inspected afterwards even though only the tail is
printed.

Why the filtering: this guest emits "TXM [Error]: selector: 38 | 42" hundreds of
thousands of times, which is 99.8% of console traffic and makes an interactive
session unreadable. Lines matching --drop (default that message) are written to
the log but not printed. Pass --drop '' to see raw output.
"""
import argparse
import os
import re
import socket
import sys
import time

DEFAULT_DROP = r'TXM \[Error\]'


def connect(path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(path)
    s.setblocking(False)
    return s


def drain(s, secs, idle, drop_re, log, echo=True):
    """Read until `idle` seconds pass with no data, or `secs` total elapse."""
    deadline = time.time() + secs
    last = time.time()
    buf = b''
    shown = []
    while time.time() < deadline:
        try:
            chunk = s.recv(65536)
            if chunk:
                buf += chunk
                last = time.time()
        except BlockingIOError:
            if time.time() - last > idle:
                break
            time.sleep(0.02)
        except (ConnectionResetError, OSError):
            break
        # emit complete lines as they arrive
        while b'\n' in buf:
            line, buf = buf.split(b'\n', 1)
            text = line.decode('utf-8', 'replace').rstrip('\r')
            if log:
                log.write(text + '\n')
            if drop_re and drop_re.search(text):
                continue
            shown.append(text)
            if echo:
                print(text, flush=True)
    if buf:
        text = buf.decode('utf-8', 'replace')
        if log:
            log.write(text)
        if not (drop_re and drop_re.search(text)):
            shown.append(text)
            if echo:
                print(text, end='', flush=True)
    if log:
        log.flush()
    return shown


def main():
    p = argparse.ArgumentParser()
    p.add_argument('sock')
    p.add_argument('action', choices=['read', 'send', 'script'])
    p.add_argument('arg', nargs='?', default='')
    p.add_argument('--secs', type=float, default=10, help='max seconds to read')
    p.add_argument('--idle', type=float, default=1.5,
                   help='stop once this many seconds pass with no output')
    p.add_argument('--drop', default=DEFAULT_DROP,
                   help='regex of lines to log but not print; empty for raw')
    p.add_argument('--log', default=None, help='append all output here')
    args = p.parse_args()

    if not os.path.exists(args.sock):
        sys.exit(f'serial: no such socket: {args.sock}')

    drop_re = re.compile(args.drop) if args.drop else None
    logpath = args.log or os.path.splitext(args.sock)[0] + '.console.log'
    log = open(logpath, 'a', buffering=1)

    s = connect(args.sock)

    if args.action == 'read':
        drain(s, args.secs, args.idle, drop_re, log)
    elif args.action == 'send':
        drain(s, 0.3, 0.2, drop_re, log, echo=False)   # discard backlog
        s.sendall(args.arg.encode() + b'\n')
        drain(s, args.secs, args.idle, drop_re, log)
    elif args.action == 'script':
        with open(args.arg) as f:
            cmds = [l.rstrip('\n') for l in f if l.strip() and not l.startswith('#')]
        drain(s, 0.3, 0.2, drop_re, log, echo=False)
        for c in cmds:
            print(f'--- {c}', flush=True)
            s.sendall(c.encode() + b'\n')
            drain(s, args.secs, args.idle, drop_re, log)
    s.close()
    log.close()


if __name__ == '__main__':
    main()
