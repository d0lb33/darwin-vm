#!/usr/bin/env python3
"""Bounded exact-cache metadata scan; static references are NOT receiver traces."""
import argparse,hashlib,json,re,subprocess,time
from pathlib import Path
IMAGES=['QuartzCore','RenderBox','UIKitCore','Metal','IOSurface','HDRProcessing']
RELEVANT=re.compile(r'MTL|Metal|IOSurface|[Ss]haredEvent|[Ff]ence|[Tt]exture|[Cc]ommandBuffer|[Cc]ommandEncoder|[Rr]enderPipeline|[Cc]omputePipeline|supports[A-Z]|[Mm]emoryless|[Rr]asterOrder|[Pp]rotectionOptions|[Mm]etallib')
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        while b:=f.read(1024*1024):h.update(b)
    return h.hexdigest()
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('cache',type=Path);p.add_argument('backboardd',type=Path);p.add_argument('out',type=Path)
    p.add_argument('--extract',action='store_true');p.add_argument('--seconds-per-command',type=int,default=25)
    a=p.parse_args();a.out.mkdir(exist_ok=False);start=time.monotonic();records=[];summary=[]
    def run(label,args):
        out=a.out/(label+'.txt');err=a.out/(label+'.stderr');t=time.monotonic();timed=False
        with out.open('wb') as f,err.open('wb') as e:
            try:result=subprocess.run(args,stdout=f,stderr=e,timeout=a.seconds_per_command);code=result.returncode
            except subprocess.TimeoutExpired:code=None;timed=True
        # ipsw can return zero while printing an argument/lookup error.
        diagnostic=err.read_text(errors='replace');failed=timed or code!=0 or '⨯' in diagnostic or 'Usage:' in diagnostic
        records.append(dict(label=label,argv=args,exit=code,timeout=timed,failed=failed,seconds=time.monotonic()-t,
                            stdout_sha256=sha(out),stderr_sha256=sha(err)))
        return out if not failed else None
    for name in IMAGES:
        outputs=[]
        for mode,flags in [('objc',['--objc','--objc-refs']),('strings',['--strings']),('loads',['--loads'])]:
            out=run(name+'.'+mode,['ipsw','dyld','macho',str(a.cache),name,*flags])
            if out:outputs.append(out)
        refs=[]
        for out in outputs:
            for no,line in enumerate(out.read_text(errors='replace').splitlines(),1):
                if RELEVANT.search(line):refs.append(dict(file=out.name,line=no,text=line))
        (a.out/(name+'.references.json')).write_text(json.dumps(dict(image=name,evidence='static metadata/string/import reference only',
            receiver='unknown unless class declaration explicitly names it; selector references do not establish caller receiver',
            runtime_observed=False,implemented='not inferred from metadata',verified=False,references=refs),indent=2)+'\n')
        summary.append(dict(image=name,matched_lines=len(refs)))
        if a.extract:
            dest=a.out/'images';dest.mkdir(exist_ok=True)
            run(name+'.extract',['ipsw','dyld','extract',str(a.cache),name,'--output',str(dest),'--cache',str(a.out/'symbols.a2s')])
    run('Metal.reverse-imports',['ipsw','dyld','imports',str(a.cache),'Metal'])
    run('IOSurface.reverse-imports',['ipsw','dyld','imports',str(a.cache),'IOSurface'])
    # Loose executable metadata is scanned separately from cache images.
    run('backboardd.objc',['ipsw','macho','info',str(a.backboardd),'--objc'])
    run('backboardd.imports',['nm','-u',str(a.backboardd)])
    run('backboardd.strings',['strings','-a',str(a.backboardd)])
    extracted=[dict(path=str(f.relative_to(a.out)),bytes=f.stat().st_size,sha256=sha(f)) for f in (a.out/'images').rglob('*') if f.is_file()] if (a.out/'images').exists() else []
    # UUID/header provenance is lightweight; no claim of a new IPSW extraction
    # or whole-cache digest. Link this run to the existing exact-guest package.
    with a.cache.open('rb') as f:header=f.read(4096)
    report=dict(scope='bounded metadata/reference survey, no whole-OS decompilation',cache=str(a.cache.resolve()),
        cache_header_sha256=hashlib.sha256(header).hexdigest(),backboardd_sha256=sha(a.backboardd),
        source_note='existing exact 24A5430a extraction; this run does not re-attest IPSW provenance',
        seconds=time.monotonic()-start,summary=summary,commands=records,extracted=extracted,
        unknowns=['indirect Objective-C receivers','dynamic selectors and C++ indirect calls','capability branch reachability','resource and synchronization semantics not encoded in method names'])
    (a.out/'index.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(seconds=report['seconds'],summary=summary,failed=[r['label'] for r in records if r['failed']])))
if __name__=='__main__':main()
