#!/usr/bin/env python3
"""Build and check diagnostic forwarding pieces without launching a guest."""
import argparse
import json
from pathlib import Path
import subprocess
import time


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    a.output.mkdir(exist_ok=False)
    root=Path(__file__).resolve().parent
    out=a.output.resolve()
    records=[]
    common=['xcrun','clang','-fobjc-arc','-Wall','-Wextra','-Werror']
    frameworks=['-framework','Foundation','-framework','Metal','-framework','IOSurface']
    commands=[
        ('server-build',common+[str(root/'metal_proxy_server.m')]+frameworks+['-o',str(out/'metal_proxy_server')]),
        ('wrapper-build',common+['-bundle',str(root/'guest_forwarding.m')]+frameworks+['-o',str(out/'DVMForward.bundle')]),
        ('work-build',common+[str(root/'guest_work.m')]+frameworks+['-o',str(out/'guest-work')]),
        ('negative-build',common+[str(root/'test_forwarding_negative.m')]+frameworks+['-o',str(out/'test-negative')]),
        ('parser-build',['xcrun','clang','-Wall','-Wextra','-Werror',str(root/'test_guest_transport.c'),'-o',str(out/'test-transport')]),
        ('parser-test',[str(out/'test-transport')]),
        ('wrapper-test',[str(out/'guest-work'),'--spawn',str(out/'DVMForward.bundle'),str(out/'metal_proxy_server'),str(a.library.resolve())]),
        ('negative-test',[str(out/'test-negative'),str(out/'DVMForward.bundle')]),
        ('uart-test',['python3',str(root/'test_proxy_uart.py'),'--harness',str(out/'guest-work'),'--bundle',str(out/'DVMForward.bundle'),'--worker',str(out/'metal_proxy_server'),'--library',str(a.library.resolve()),'--output',str(out/'uart')]),
    ]
    try:
        for name,argv in commands:
            started=time.monotonic()
            with (out/f'{name}.stdout').open('wb') as stdout, (out/f'{name}.stderr').open('wb') as stderr:
                result=subprocess.run(argv,stdout=stdout,stderr=stderr,timeout=60)
            records.append(dict(name=name,argv=argv,returncode=result.returncode,seconds=time.monotonic()-started))
            result.check_returncode()
            print(name+': pass',flush=True)
    finally:
        (out/'commands.json').write_text(json.dumps(records,indent=2)+'\n')


if __name__=='__main__':
    main()
