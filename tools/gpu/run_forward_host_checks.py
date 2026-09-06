#!/usr/bin/env python3
"""Build and check diagnostic forwarding pieces without launching a guest."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import hashlib
from verify_roundtrip import SHA,require


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    a.output.mkdir(exist_ok=False)
    root=Path(__file__).resolve().parent
    out=a.output.resolve()
    # The exact fat resource's already verified, unchanged AIR slice.
    air=a.library.read_bytes()[0x30:0x30+2705796]
    require(hashlib.sha256(air).hexdigest()==SHA,'wrong exact-guest AIR resource')
    (out/'exact-air.metallib').write_bytes(air)
    records=[]
    common=['xcrun','clang','-fobjc-arc','-Wall','-Wextra','-Werror']
    frameworks=['-framework','Foundation','-framework','Metal','-framework','IOSurface']
    commands=[
        ('server-build',common+[str(root/'metal_proxy_server.m')]+frameworks+['-o',str(out/'metal_proxy_server')]),
        ('wrapper-build',common+['-bundle',str(root/'guest_forwarding.m')]+frameworks+['-o',str(out/'DVMForward.bundle')]),
        ('work-build',common+[str(root/'guest_work.m')]+frameworks+['-o',str(out/'guest-work')]),
        ('negative-build',common+[str(root/'test_forwarding_negative.m')]+frameworks+['-o',str(out/'test-negative')]),
        ('parser-build',['xcrun','clang','-Wall','-Wextra','-Werror',str(root/'test_guest_transport.c'),'-o',str(out/'test-transport')]),
        ('v2-build',['xcrun','clang','-Wall','-Wextra','-Werror','-DDVM_HOST_TEST',str(root/'guest_transport_v2.c'),'-o',str(out/'transport-v2')]),
        ('codec-build',['xcrun','clang','-Wall','-Wextra','-Werror',str(root/'test_uart_codec.c'),'-o',str(out/'test-codec')]),
        ('codec-test',[str(out/'test-codec')]),
        ('parser-test',[str(out/'test-transport')]),
        ('wrapper-test',[str(out/'guest-work'),'--spawn',str(out/'DVMForward.bundle'),str(out/'metal_proxy_server'),str(a.library.resolve())]),
        ('negative-test',[str(out/'test-negative'),str(out/'DVMForward.bundle')]),
        ('uart-test',['python3',str(root/'test_proxy_uart.py'),'--harness',str(out/'guest-work'),'--bundle',str(out/'DVMForward.bundle'),'--worker',str(out/'metal_proxy_server'),'--library',str(a.library.resolve()),'--output',str(out/'uart')]),
    ]
    v2=['python3',str(root/'test_uart_v2.py'),'--transport',str(out/'transport-v2'),
        '--worker',str(out/'metal_proxy_server'),'--harness',str(out/'guest-work'),
        '--bundle',str(out/'DVMForward.bundle'),'--library',str(a.library.resolve()),'--cache',str(out/'exact-air.metallib')]
    commands += [('v2-noise-test',v2+['--output',str(out/'v2-noise')]),
        ('v2-disconnect-test',v2+['--disconnect','--output',str(out/'v2-disconnect')]),
        ('cache-negative-test',['python3',str(root/'test_library_reference.py'),'--worker',str(out/'metal_proxy_server'),'--cache',str(out/'exact-air.metallib'),'--output',str(out/'cache-negative.json')]),
        ('evidence-negative-test',['python3','-O',str(root/'test_roundtrip_evidence.py'),str(out/'v2-noise')])]
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
