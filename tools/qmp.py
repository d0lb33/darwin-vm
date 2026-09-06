#!/usr/bin/env python3
"""qmp.py - send one QMP command to a running guest's QMP unix socket.

    tools/qmp.py /tmp/dvm/RUN/qmp.sock qom-set path=/machine/smc-battery property=external value=false
    tools/qmp.py /tmp/dvm/RUN/qmp.sock qom-get path=/machine/smc-battery property=soc

Arguments are key=value pairs; values parse as JSON when they can (false,
true, numbers, quoted strings) and fall back to plain strings.  Uses the
QMP socket rather than the HMP monitor so it never collides with a probe's
own monitor connection.
"""
import json
import socket
import sys


def parse(value):
    try:
        return json.loads(value)
    except ValueError:
        return value


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    path, command = sys.argv[1], sys.argv[2]
    args = {k: parse(v) for k, v in (a.split('=', 1) for a in sys.argv[3:])}
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(30)
    s.connect(path)
    f = s.makefile('rw')
    f.readline()  # greeting
    for msg in ({'execute': 'qmp_capabilities'}, {'execute': command, 'arguments': args}):
        f.write(json.dumps(msg) + '\n')
        f.flush()
        reply = json.loads(f.readline())
        if 'error' in reply:
            sys.exit(json.dumps(reply))
    print(json.dumps(reply))


if __name__ == '__main__':
    main()
