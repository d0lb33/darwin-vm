#!/usr/bin/env python3
"""Cache misses must fail explicitly, never substitute another shader library."""
import argparse
import errno
import json
import os
import subprocess
from pathlib import Path
from verify_roundtrip import SHA,require

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('worker','cache','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();results=[]
    for name,count,digest,path,code in [('missing',2705796,SHA,None,errno.ENOENT),('length',1,SHA,a.cache,errno.ENOENT),('hash',2705796,'0'*64,a.cache,errno.EILSEQ)]:
        env=os.environ.copy();env.pop('DVM_PROXY_LIBRARY_CACHE',None)
        if path:env['DVM_PROXY_LIBRARY_CACHE']=str(path)
        cmd=f'LIBREF 1 {count} {digest}\n'
        r=subprocess.run([str(a.worker)],input=cmd,text=True,capture_output=True,env=env,timeout=10)
        require(r.returncode!=0 and r.stdout==f'ERR 1 LIBCACHE {code}\n',name+' unexpectedly accepted')
        results.append(dict(case=name,command=cmd,exit=r.returncode,stdout=r.stdout,stderr=r.stderr))
    a.output.write_text(json.dumps(results,indent=2)+'\n');print('PASS missing cache, wrong length, wrong digest')
if __name__=='__main__':main()
