#!/usr/bin/env python3
"""Minimal QEMU human monitor (HMP) client over a unix socket.

usage: hmp.py <socket> [--wait SECONDS] <command...>

Examples:
    hmp.py /tmp/dvm/probe.sock info registers
    hmp.py /tmp/dvm/probe.sock --wait 45 screendump /tmp/shot.png -f png
    hmp.py /tmp/dvm/probe.sock 'x/32gx 0xfffffff027de2260'

Notes:
  - UNIX socket paths must stay under ~104 bytes, so keep sockets in /tmp/dvm.
  - The monitor echoes typed characters; this strips the echo and returns the
    reply text only.
"""
import socket
import sys
import time


def read_until_prompt(s):
    buf = b""
    while not buf.rstrip().endswith(b"(qemu)"):
        try:
            chunk = s.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
    return buf.decode(errors="replace")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    sock_path = sys.argv[1]
    args = sys.argv[2:]

    if args and args[0] == "--wait":
        time.sleep(float(args[1]))
        args = args[2:]

    cmd = " ".join(args)

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(20)
    s.connect(sock_path)

    read_until_prompt(s)
    s.sendall((cmd + "\n").encode())
    out = read_until_prompt(s)

    # Drop the command echo (monitor echoes each keystroke with ANSI edits)
    lines = []
    for line in out.replace("\r", "").split("\n"):
        if "\x1b[" in line or line.strip() == "(qemu)":
            continue
        lines.append(line)
    print("\n".join(lines).strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
