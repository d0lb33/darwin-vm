#!/usr/bin/env python3
"""Verify the bounded exact-guest pin experiment without claiming GPU import."""
import argparse,base64,hashlib,json,re,struct,zlib
from pathlib import Path
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('run',type=Path);a=p.parse_args()
    result=json.loads((a.run/'result.json').read_text());lines=[];last=0;session=None
    for raw in (a.run/'driver-audit.jsonl').read_text().splitlines():
        r=json.loads(raw);line=r['line'];slot=r['slot_v1']
        b=base64.b64decode(slot['bytes'],validate=True);seq,n,crc=struct.unpack_from('<QII',b)
        assert seq==r['seq']==last+1 and len(b)==16+n and zlib.crc32(b[16:])==crc
        assert b[16:].decode().rstrip('\n')==line and slot['head']>=seq
        if session is None:session=slot['session']
        assert slot['session']==session
        last=seq
        if 'GPU_LOAD_SURFACE_' in line:lines.append(line)
    meta=[l for l in lines if l.startswith('GPU_LOAD_SURFACE_META ')];assert len(meta)==1
    def fields(line):return dict(re.findall(r'(\w+)=(\w+)',line))
    m=fields(meta[0]);length=int(m['bytes']);offset=int(m['base_offset']);pages=(offset+length+16383)//16384
    assert int(m['nonnull'])==1 and int(m['row'])*int(m['height'])<=length
    assert 0<=offset<16384 and int(m['tail'])==length%16384
    calls=[fields(l) for l in lines if l.startswith('GPU_LOAD_SURFACE_PIN ')];assert len(calls)==5
    for i,r in enumerate(calls):
        assert int(r['trial'])==i and int(r['count'])==4
        if i<2:assert int(r['kr'],16)==0xe00002c2 and int(r['stage'])==0
        else:assert int(r['kr'],16)==0 and int(r['stage'])==5 and int(r['pages'])==pages and int(r['bytes'])==length and int(r['complete'])==0
    assert not any('PIN_STOP' in l for l in lines)
    assert result['registered'] and not result['debugger'] and not result['ram_restored']
    assert not result['render_submissions'] and not result['blit_submissions'] and not result['final_pixels_verified']
    verdict=dict(scope='actual backboardd existing IOSurface prepare/page-validation/complete only',verified=True,
        metadata=m,pages=pages,successful_cycles=3,negative_controls=2,
        audit_sha256=hashlib.sha256((a.run/'driver-audit.jsonl').read_bytes()).hexdigest(),
        pinned_inputs=json.loads((a.run/'source-manifest.json').read_text())['qemu_inputs'],
        host_alias=False,gpu_import=False,display_retirement=False,elapsed=result['elapsed'])
    (a.run/'pin-verification.json').write_text(json.dumps(verdict,indent=2)+'\n')
    print(json.dumps({k:v for k,v in verdict.items() if k!='pinned_inputs'}))
if __name__=='__main__':main()
