#!/usr/bin/env python3
"""Reject corrupted evidence, including when Python optimization is enabled."""
import argparse
from pathlib import Path
import shutil
import tempfile
from verify_roundtrip import verify,require

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('record',type=Path);a=p.parse_args()
    verify(a.record)
    for name in ('response-byte','request-byte','gpu-witness','guest-report'):
        with tempfile.TemporaryDirectory(prefix='gpu-evidence-') as tmp:
            out=Path(tmp)
            for item in ('guest-requests.bin','host-responses.bin','host-worker.stderr'):shutil.copyfile(a.record/item,out/item)
            if name in ('response-byte','request-byte'):
                file=out/('host-responses.bin' if name=='response-byte' else 'guest-requests.bin')
                b=bytearray(file.read_bytes());b[1024]^=1;file.write_bytes(b)
            else:
                file=out/'host-worker.stderr';s=file.read_text()
                s=s.replace('pre_dispatch_differs=1','pre_dispatch_differs=0',1) if name=='gpu-witness' else s.replace('DIAG event=guest-verification ','DIAG event=discarded ',1)
                file.write_text(s)
            rejected=False
            try:verify(out)
            except (ValueError,KeyError,IndexError):rejected=True
            require(rejected,'accepted corrupt evidence: '+name)
            print('rejected '+name)
if __name__=='__main__':main()
