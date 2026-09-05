#!/usr/bin/env python3
"""Send one bounded QEMU mouse gesture through QMP, exercising host capture."""
import argparse
import json
import socket
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('qmp')
    parser.add_argument('--from', dest='start', type=int, nargs=2, required=True,
                        metavar=('X','Y'), help='absolute QEMU coordinates 0..32767')
    parser.add_argument('--to', dest='end', type=int, nargs=2)
    parser.add_argument('--duration', type=float, default=.15)
    args = parser.parse_args()
    end = args.end or args.start
    if not all(0 <= v <= 32767 for v in args.start+end) or not .05 <= args.duration <= 3:
        parser.error('coordinates must be 0..32767 and duration .05..3 seconds')
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(5)
        sock.connect(args.qmp)
        stream = sock.makefile('rwb',buffering=0)
        json.loads(stream.readline())
        def call(name,arguments=None):
            request = {'execute':name,'id':name}
            if arguments is not None:
                request['arguments'] = arguments
            stream.write(json.dumps(request).encode()+b'\n')
            while True:
                reply = json.loads(stream.readline())
                if reply.get('id') == name:
                    if 'error' in reply:
                        raise RuntimeError(reply['error'])
                    return
        def event(point,down):
            events = {'events':[
                {'type':'abs','data':{'axis':'x','value':point[0]}},
                {'type':'abs','data':{'axis':'y','value':point[1]}},
                {'type':'btn','data':{'button':'left','down':down}}]}
            # LLDB's auto-continuing observer briefly stops the VM. QMP
            # rejects input during that stop; retry only this explicit
            # pre-dispatch rejection, never an ambiguous socket failure.
            deadline = time.monotonic()+5
            while True:
                try:
                    call('input-send-event',events)
                    return
                except RuntimeError as error:
                    if 'VM not running' not in str(error) or time.monotonic() >= deadline:
                        raise
                    time.sleep(.02)
        call('qmp_capabilities')
        event(args.start,True)
        try:
            count = 10 if args.end else 1
            for i in range(1,count+1):
                time.sleep(args.duration/count)
                if args.end:
                    event([round(a+(b-a)*i/count) for a,b in zip(args.start,end)],True)
        finally:
            event(end,False)
        print(json.dumps({'sent':True,'from':args.start,'to':end,'duration':args.duration}))


if __name__ == '__main__':
    main()
