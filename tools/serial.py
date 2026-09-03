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
    if not wait_for_text(s, prompt_re, timeout, drop_re, log):
        return False
    # If neither sacrificial newline was dropped, the shell emits two prompts.
    # wait_for_text returns as soon as it sees the first one; sending the real
    # command immediately lets the second prompt bisect the terminal echo
    # (for example ``mount_# apfs``).  Drain that bounded remainder and require
    # a quiet prompt boundary before transmitting bytes whose echo we verify.
    drain(s, 0.5, 0.10, drop_re, log, echo=False)
    return True


def send_command(s, command, char_delay):
    """Transmit a command behind a prefix valid with or without byte one."""
    # If an idle UART drops the first byte, ``: :; command`` becomes
    # `` :; command``: both forms run the POSIX `:` no-op and then `command`.
    # A leading space alone is not enough because some console paths discard it
    # before losing the first printable byte.  Unlike a leading newline, this
    # cannot make the real command become a new line's first byte.
    send_paced(s, b': :; ' + command.encode() + b'\n', char_delay)


def send_command_batch(s, commands, char_delay):
    """Send several independently guarded shell lines without an idle gap.

    A batch is deliberately just a sequence of normal, short shell lines: it
    does not make a larger shell input record.  Keeping the ``: :;`` guard on
    *each* line means an unusual receiver which drops a byte after a command
    response still cannot turn a payload command into a different command.
    The caller must use an independent guest witness for every line.
    """
    wire = b''.join(b': :; ' + command.encode() + b'\n'
                    for command in commands)
    send_paced(s, wire, char_delay)


def wait_for_markers(s, markers, secs, drop_re, log, echo=True):
    """Wait until every marker appears in one post-send console stream.

    ``wait_for_text`` intentionally returns as soon as a single regex matches.
    That is unsuitable for a batch because one recv() can contain several
    cksum witnesses; returning after the first would discard the rest from the
    caller's point of view.  This helper preserves the same logging behaviour
    while proving that every command in a batch completed with its own marker.
    """
    wanted = {marker: re.compile(re.escape(marker)) for marker in markers}
    seen = set()
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
        for marker, pattern in wanted.items():
            if pattern.search(text_buf):
                seen.add(marker)
        while b'\n' in line_buf:
            line, line_buf = line_buf.split(b'\n', 1)
            text = line.decode('utf-8', 'replace').rstrip('\r')
            if log:
                log.write(text + '\n')
            if not (drop_re and drop_re.search(text)) and echo:
                print(text, flush=True)
        if len(seen) == len(wanted):
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


def remote_path_is_safe(path):
    """Limit uploads to disposable Data/User staging namespaces."""
    roots = ('/private/var/.dvm-data-seed/',
             '/private/var/hardware/.dvm-data-seed/')
    return (any(path.startswith(root) and path != root for root in roots) and
            '..' not in path.split('/'))


def upload(s, local_path, remote_path, char_delay, prompt_re, args,
           drop_re, log):
    """Upload a local file via the restore shell's shipped /bin/base64.

    This deliberately does not add a binary to an image on the host.  Each
    shell command is handshake/echo checked like ordinary serial commands,
    while the final byte count and checksum witness that the decoder did work.
    Chunks are written to fixed-width numbered files by the shell builtin, then
    checked with the guest's cksum before they are accepted; a shell RC alone
    only proves printf ran and does not detect a dropped payload byte.  One
    guest decoder runs after every chunk is proven.  A missing or mismatched
    chunk witness can therefore retry that fixed path without duplicating data.
    The target namespaces are intentionally narrow.  Protected staging files
    must be uploaded, executed, and removed in the same guest keybag session;
    their file keys are not reusable by a later restore boot.
    """
    if not remote_path_is_safe(remote_path):
        sys.exit('serial: upload target must be in a supported .dvm-data-seed namespace')
    try:
        with open(local_path, 'rb') as f:
            blob = f.read()
    except OSError as e:
        sys.exit(f'serial: cannot read upload source {local_path}: {e}')

    def checked(command, marker, final=False, attempts=1):
        """Run a witnessed upload command, retrying only idempotent writes."""
        for attempt in range(1, attempts + 1):
            if not wait_for_prompt(s, prompt_re, args.prompt_timeout,
                                   char_delay, drop_re, log):
                if attempt != attempts:
                    continue
                sys.exit('serial: shell prompt did not arrive before upload command')
            send_command(s, command, char_delay)
            # Kernel and launchd logs may bisect terminal echo.  A numbered
            # completion marker proves the shell parsed and ran this command;
            # the final cksum proves all accepted chunks decoded exactly.
            if wait_for_text(s, re.compile(re.escape(marker)),
                             args.echo_timeout, drop_re, log):
                drain(s, args.secs if final else 0.10,
                      args.idle if final else 0.05, drop_re, log)
                return
            if attempt != attempts:
                # A truncated UART line may leave the shell waiting for input.
                # Ctrl-C plus the normal two-newline handshake restores a
                # command boundary.  Chunk writes use `>` below, so resending
                # one can never duplicate bytes.
                send_paced(s, b'\x03\n', char_delay)
                drain(s, 0.5, 0.2, drop_re, log, echo=False)
        sys.exit('serial: upload completion marker did not arrive')

    def checked_batch(entries, attempts=1):
        """Run short, idempotent writes under one prompt handshake.

        Each entry retains a guest cksum witness.  If any witness is absent,
        retry the whole batch after restoring a shell boundary.  The writes use
        ``>``, so overwriting an already accepted part cannot append or make a
        good transfer look complete.  Batches are only used for transport part
        files; setup and final verification remain individually witnessed.
        """
        commands = [command for command, _ in entries]
        markers = [marker for _, marker in entries]
        for attempt in range(1, attempts + 1):
            if not wait_for_prompt(s, prompt_re, args.prompt_timeout,
                                   char_delay, drop_re, log):
                if attempt != attempts:
                    continue
                sys.exit('serial: shell prompt did not arrive before upload batch')
            send_command_batch(s, commands, char_delay)
            if wait_for_markers(s, markers, args.echo_timeout, drop_re, log):
                drain(s, 0.10, 0.05, drop_re, log)
                return
            if attempt != attempts:
                # A truncated line can leave the shell consuming continuation
                # input.  The identical recovery used by checked() restores a
                # boundary before idempotently replacing every part in batch.
                send_paced(s, b'\x03\n', char_delay)
                drain(s, 0.5, 0.2, drop_re, log, echo=False)
        sys.exit('serial: upload batch completion markers did not all arrive')

    # mkdir is intentionally only the unique parent created by this tool.
    remote_dir = os.path.dirname(remote_path)
    encoded_path = remote_path + '.b64'
    old_part_glob = encoded_path + '.part.*'
    # Keep each checksummed write line below the restore shell's observed
    # roughly-256-byte safe envelope.  The former target-derived part names
    # left no room for a content witness on the same line.
    part_dir = remote_dir + '/.upload-parts'
    part_glob = part_dir + '/*'
    # Remove an earlier decoded target as well as transport fragments.  A
    # protected APFS inode from a prior restore boot may no longer be
    # unwrap-able in the current key state; truncating that inode makes a
    # byte-perfect upload look corrupt even though unlinking it and creating a
    # fresh staging inode is valid inside this disposable namespace.
    checked("mkdir -p %s %s; echo DVM_UPLOAD_SETUP_DIR_RC=$?" %
            (remote_dir, part_dir), 'DVM_UPLOAD_SETUP_DIR_RC=0', attempts=3)
    checked("/bin/rm -f %s %s; echo DVM_UPLOAD_SETUP_TARGET_RC=$?" %
            (remote_path, encoded_path),
            'DVM_UPLOAD_SETUP_TARGET_RC=0', attempts=3)
    checked("/bin/rm -f %s %s; echo DVM_UPLOAD_SETUP_RC=$?" %
            (old_part_glob, part_glob), 'DVM_UPLOAD_SETUP_RC=0', attempts=3)
    # Keep an individual line comfortably below the shell's and UART's input
    # limits.  Base64 has no quoting metacharacters, so single quotes are safe.
    encoded = base64.b64encode(blob).decode('ascii')
    # The restore shell silently loses characters on a roughly 500-byte input
    # line (payload plus the decoder command), not merely on the 2 KiB lines
    # that visibly ring its bell.  Keep the complete shell line below 256 B.
    # It is slower, but every accepted chunk must be exact before a trust-cache
    # controlled guest executable is allowed to run.
    sample_part = '%s/%06d' % (part_dir, 1)
    line_overhead = len((": :; printf '%%s' '' > %s; /bin/cksum %s\n" %
                         (sample_part, sample_part)).encode('ascii'))
    # Reserve a four-byte-aligned Base64 payload inside a 252-byte transmitted
    # line.  User staging has a longer prefix than Data staging, so a fixed
    # payload size that is safe for one is not necessarily safe for the other.
    chunk_bytes = min(120, ((252 - line_overhead) // 4) * 4)
    if chunk_bytes <= 0:
        sys.exit('serial: upload target leaves no room for a safe chunk line')
    entries = []
    for number, off in enumerate(range(0, len(encoded), chunk_bytes), 1):
        chunk = encoded[off:off + chunk_bytes]
        part_path = '%s/%06d' % (part_dir, number)
        try:
            chunk_cksum = subprocess.check_output(
                ['cksum'], input=chunk.encode('ascii')).decode().split()[0]
        except (OSError, subprocess.CalledProcessError) as e:
            sys.exit(f'serial: cannot calculate chunk cksum: {e}')
        marker = '%s %d %s' % (chunk_cksum, len(chunk), part_path)
        entries.append(("printf '%%s' '%s' > %s; /bin/cksum %s" %
                        (chunk, part_path, part_path), marker))
        if len(entries) == args.upload_batch:
            checked_batch(entries, attempts=8)
            entries = []
    if entries:
        checked_batch(entries, attempts=8)

    witness = 'DVM_UPLOAD_FINAL_RC=0 DVM_UPLOAD_BYTES=%d' % len(blob)
    try:
        cksum = subprocess.check_output(['cksum', local_path], text=True).split()[0]
    except (OSError, subprocess.CalledProcessError) as e:
        sys.exit(f'serial: cannot calculate host cksum: {e}')
    # Keep the join/decode/verification lines independently witnessed and below
    # the UART's safe input length too.  A former all-in-one final command was
    # itself long enough to be corrupted, making good chunks look bad.
    checked("/bin/cat %s > %s; echo DVM_UPLOAD_JOIN_RC=$?" %
            (part_glob, encoded_path), 'DVM_UPLOAD_JOIN_RC=0', attempts=3)
    checked("/bin/base64 -d %s > %s; echo DVM_UPLOAD_DECODE_RC=$?" %
            (encoded_path, remote_path), 'DVM_UPLOAD_DECODE_RC=0', attempts=3)
    checked("test \"$(wc -c < %s)\" -eq %d; echo DVM_UPLOAD_SIZE_RC=$?" %
            (remote_path, len(blob)), 'DVM_UPLOAD_SIZE_RC=0', attempts=3)
    # The restore ramdisk deliberately has a small userland; let POSIX sh split
    # cksum's "sum bytes filename" output instead of depending on awk.
    checked("set -- $(cksum %s); test \"$1\" = %s; echo DVM_UPLOAD_CKSUM_RC=$?" %
            (remote_path, cksum), 'DVM_UPLOAD_CKSUM_RC=0', attempts=3)
    checked("/bin/rm -f %s %s; /bin/rmdir %s; echo DVM_UPLOAD_FINAL_RC=$? DVM_UPLOAD_BYTES=%d" %
            (encoded_path, part_glob, part_dir, len(blob)), witness,
            final=True, attempts=3)


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
    p.add_argument('--upload-batch', type=int, default=4,
                   help='independently checksummed upload part lines per prompt')
    p.add_argument('--no-handshake', action='store_true',
                   help='send directly; only for consoles that are not shells')
    p.add_argument('--remote-path',
                   help='guest destination in an allowed .dvm-data-seed staging namespace')
    args = p.parse_args()

    if args.upload_batch < 1:
        p.error('--upload-batch must be at least one')

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
