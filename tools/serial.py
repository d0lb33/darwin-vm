#!/usr/bin/env python3
"""serial.py - drive a guest serial console over a QEMU unix socket.

Boot qemu with a socket-backed serial port instead of a file or stdio:

    -serial unix:/tmp/dvm/sh.sock,server,nowait

then talk to it from a script:

    tools/serial.py /tmp/dvm/sh.sock read --secs 5      # drain what is there
    tools/serial.py /tmp/dvm/sh.sock send 'mount -uw /' # send a line, show reply
    tools/serial.py /tmp/dvm/sh.sock script cmds.txt    # one command per line

Before sending a command, ``send`` and ``script`` resynchronise with the shell:
they send blank input, wait for its prompt, pace the command, and require the
console echo to contain the command.  A UART can discard the first byte after
an idle period; the blank input is deliberately sacrificial, so a dropped byte
cannot turn ``echo`` into ``cho`` and make a failed command look successful.
Use ``--no-handshake`` only for a console that is not a shell.

Everything the guest sends is appended to a log (--log, default alongside the
socket) so a long boot can be inspected afterwards even though only the tail is
printed.

Why the filtering: this guest emits "TXM [Error]: selector: 38 | 42" hundreds of
thousands of times, which is 99.8% of console traffic and makes an interactive
session unreadable. Lines matching --drop (default that message) are written to
the log but not printed. Pass --drop '' to see raw output.
"""
import argparse
import base64
import subprocess
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


def send_paced(s, data, char_delay):
    """Send bytes slowly enough for the guest's UART line discipline to settle."""
    for byte in data:
        s.sendall(bytes((byte,)))
        if char_delay:
            time.sleep(char_delay)


def wait_for_text(s, pattern, secs, drop_re, log, echo=True):
    """Log and display input until a regex appears in the raw console stream."""
    deadline = time.time() + secs
    line_buf = b''
    text_buf = ''
    while time.time() < deadline:
        try:
            chunk = s.recv(65536)
        except BlockingIOError:
            time.sleep(0.02)
            continue
        except (ConnectionResetError, OSError):
            return False
        if not chunk:
            return False

        line_buf += chunk
        text_buf = (text_buf + chunk.decode('utf-8', 'replace'))[-16384:]
        while b'\n' in line_buf:
            line, line_buf = line_buf.split(b'\n', 1)
            text = line.decode('utf-8', 'replace').rstrip('\r')
            if log:
                log.write(text + '\n')
            if not (drop_re and drop_re.search(text)) and echo:
                print(text, flush=True)
        if pattern.search(text_buf):
            if line_buf:
                text = line_buf.decode('utf-8', 'replace')
                if log:
                    log.write(text)
                if not (drop_re and drop_re.search(text)) and echo:
                    print(text, end='', flush=True)
            if log:
                log.flush()
            return True

    if line_buf:
        text = line_buf.decode('utf-8', 'replace')
        if log:
            log.write(text)
        if not (drop_re and drop_re.search(text)) and echo:
            print(text, end='', flush=True)
    if log:
        log.flush()
    return False


def wait_for_prompt(s, prompt_re, timeout, char_delay, drop_re, log):
    """Establish a shell boundary without risking the first real command byte.

    Two newlines are intentional.  If the UART drops the first byte after an
    idle period, the second still asks the shell for its prompt; if it does not,
    two empty commands are harmless.
    """
    # connect() only queues the client for QEMU's main loop; give that loop a
    # chance to install its chardev watches before the sacrificial input.
    time.sleep(0.05)
    send_paced(s, b'\n\n', char_delay)
    return wait_for_text(s, prompt_re, timeout, drop_re, log)


def send_command(s, command, char_delay):
    """Transmit a command behind a prefix valid with or without byte one."""
    # If an idle UART drops the first byte, ``: :; command`` becomes
    # `` :; command``: both forms run the POSIX `:` no-op and then `command`.
    # A leading space alone is not enough because some console paths discard it
    # before losing the first printable byte.  Unlike a leading newline, this
    # cannot make the real command become a new line's first byte.
    send_paced(s, b': :; ' + command.encode() + b'\n', char_delay)


def remote_path_is_safe(path):
    """Limit uploads to the disposable Data-volume staging namespace."""
    return (path.startswith('/private/var/.dvm-data-seed/') and
            '..' not in path.split('/') and
            path != '/private/var/.dvm-data-seed/')


def upload(s, local_path, remote_path, char_delay, prompt_re, args,
           drop_re, log):
    """Upload a local file via the restore shell's shipped /bin/base64.

    This deliberately does not add a binary to an image on the host.  Each
    shell command is handshake/echo checked like ordinary serial commands,
    while the final byte count is a guest-side witness that the decoder did
    work.  Chunks are appended as base64 text by the shell builtin; one guest
    decoder runs only after the complete text has arrived.  That avoids
    repeatedly spawning base64 (and flooding the console with policy logs).
    The target namespace is intentionally narrow: it is only writable
    after the caller mounted the disposable Data volume at /private/var.
    """
    if not remote_path_is_safe(remote_path):
        sys.exit('serial: upload target must be below /private/var/.dvm-data-seed/')
    try:
        with open(local_path, 'rb') as f:
            blob = f.read()
    except OSError as e:
        sys.exit(f'serial: cannot read upload source {local_path}: {e}')

    def checked(command, marker, final=False):
        if not wait_for_prompt(s, prompt_re, args.prompt_timeout,
                               char_delay, drop_re, log):
            sys.exit(f'serial: shell prompt did not arrive before upload command')
        send_command(s, command, char_delay)
        # Kernel and launchd logs may bisect terminal echo.  A numbered command
        # completion marker is a stronger witness than an exact visual echo:
        # it proves the shell parsed and ran this command, and the final cksum
        # below proves all parsed chunks decoded to the intended bytes.
        if not wait_for_text(s, re.compile(re.escape(marker)),
                             args.echo_timeout, drop_re, log):
            sys.exit('serial: upload completion marker did not arrive')
        # Base64 decode of one short chunk is immediate.  Waiting the normal
        # command idle window for every chunk would turn a 50 KiB helper into a
        # ten-minute transfer, while skipping the final witness would weaken
        # the only useful success signal.  Keep the chunks paced and reserve
        # the caller-selected drain for setup and byte-count verification.
        drain(s, args.secs if final else 0.10,
              args.idle if final else 0.05, drop_re, log)

    # mkdir is intentionally only the unique parent created by this tool.
    encoded_path = remote_path + '.b64'
    checked("mkdir -p /private/var/.dvm-data-seed && : > %s; echo DVM_UPLOAD_SETUP_RC=$?" % encoded_path,
            'DVM_UPLOAD_SETUP_RC=0')
    # Keep an individual line comfortably below the shell's and UART's input
    # limits.  Base64 has no quoting metacharacters, so single quotes are safe.
    encoded = base64.b64encode(blob).decode('ascii')
    # The restore shell silently loses characters on a roughly 500-byte input
    # line (payload plus the decoder command), not merely on the 2 KiB lines
    # that visibly ring its bell.  Keep the complete shell line below 256 B.
    # It is slower, but every accepted chunk must be exact before a trust-cache
    # controlled guest executable is allowed to run.
    for number, off in enumerate(range(0, len(encoded), 128), 1):
        chunk = encoded[off:off + 128]
        marker = 'DVM_UPLOAD_CHUNK_%d_RC' % number
        checked("printf '%%s' '%s' >> %s; echo %s=$?" %
                (chunk, encoded_path, marker), marker + '=0')

    witness = 'DVM_UPLOAD_FINAL_RC=0 DVM_UPLOAD_BYTES=%d' % len(blob)
    try:
        cksum = subprocess.check_output(['cksum', local_path], text=True).split()[0]
    except (OSError, subprocess.CalledProcessError) as e:
        sys.exit(f'serial: cannot calculate host cksum: {e}')
    # The restore ramdisk deliberately has a very small userland; avoid awk
    # here and let POSIX sh split cksum's "sum bytes filename" output.
    checked("/bin/base64 -d %s > %s; dec=$?; test \"$dec\" -eq 0 && test \"$(wc -c < %s)\" -eq %d && set -- $(cksum %s) && test \"$1\" = %s; rc=$?; test \"$rc\" -ne 0 || /bin/rm -f %s; echo DVM_UPLOAD_FINAL_RC=$rc DVM_UPLOAD_BYTES=%d" %
            (encoded_path, remote_path, remote_path, len(blob), remote_path, cksum,
             encoded_path, len(blob)), witness, final=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('sock')
    p.add_argument('action', choices=['read', 'send', 'script', 'upload'])
    p.add_argument('arg', nargs='?', default='')
    p.add_argument('--secs', type=float, default=10, help='max seconds to read')
    p.add_argument('--idle', type=float, default=1.5,
                   help='stop once this many seconds pass with no output')
    p.add_argument('--drop', default=DEFAULT_DROP,
                   help='regex of lines to log but not print; empty for raw')
    p.add_argument('--log', default=None, help='append all output here')
    p.add_argument('--prompt', default=r'(?:^|[\r\n])# ?',
                   help='shell-prompt regex used for the send handshake')
    p.add_argument('--prompt-timeout', type=float, default=10,
                   help='seconds to wait for a shell prompt')
    p.add_argument('--echo-timeout', type=float, default=10,
                   help='seconds to wait for the command echo')
    p.add_argument('--char-delay', type=float, default=0.01,
                   help='seconds between transmitted UART bytes')
    p.add_argument('--no-handshake', action='store_true',
                   help='send directly; only for consoles that are not shells')
    p.add_argument('--remote-path',
                   help='guest destination for upload (only /private/var/.dvm-data-seed/*)')
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
        if args.no_handshake:
            send_paced(s, args.arg.encode() + b'\n', args.char_delay)
        else:
            prompt_re = re.compile(args.prompt)
            if not wait_for_prompt(s, prompt_re, args.prompt_timeout,
                                   args.char_delay, drop_re, log):
                sys.exit('serial: shell prompt did not arrive; command not sent')
            send_command(s, args.arg, args.char_delay)
            echo_re = re.compile(re.escape(args.arg))
            if not wait_for_text(s, echo_re, args.echo_timeout, drop_re, log):
                sys.exit('serial: command echo did not match; refusing to trust result')
        drain(s, args.secs, args.idle, drop_re, log)
    elif args.action == 'script':
        with open(args.arg) as f:
            cmds = [l.rstrip('\n') for l in f if l.strip() and not l.startswith('#')]
        drain(s, 0.3, 0.2, drop_re, log, echo=False)
        for c in cmds:
            print(f'--- {c}', flush=True)
            if args.no_handshake:
                send_paced(s, c.encode() + b'\n', args.char_delay)
            else:
                prompt_re = re.compile(args.prompt)
                if not wait_for_prompt(s, prompt_re, args.prompt_timeout,
                                       args.char_delay, drop_re, log):
                    sys.exit(f'serial: shell prompt did not arrive before: {c}')
                send_command(s, c, args.char_delay)
                echo_re = re.compile(re.escape(c))
                if not wait_for_text(s, echo_re, args.echo_timeout, drop_re, log):
                    sys.exit(f'serial: command echo did not match: {c}')
            drain(s, args.secs, args.idle, drop_re, log)
    elif args.action == 'upload':
        if args.no_handshake:
            sys.exit('serial: upload requires the shell handshake')
        if not args.remote_path:
            sys.exit('serial: upload requires --remote-path')
        drain(s, 0.3, 0.2, drop_re, log, echo=False)
        upload(s, args.arg, args.remote_path, args.char_delay,
               re.compile(args.prompt), args, drop_re, log)
    s.close()
    log.close()


if __name__ == '__main__':
    main()
