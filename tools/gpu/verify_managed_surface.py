#!/usr/bin/env python3
"""Verify a completed live-backing trial, then preserve its bounded final resource."""
import argparse,hashlib,json
from pathlib import Path
from managed_pages import read_resource,LENGTH
from present_peer import verify,verify_final,fields

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('trial',type=Path)
    p.add_argument('--require-negative',action='store_true')
    a=p.parse_args();out=a.trial
    result=json.loads((out/'result.json').read_text())
    if not result.get('passed'):raise ValueError('runtime trial failed')
    records=[json.loads(x) for x in (out/'driver-host.jsonl').read_text().splitlines()]
    accepted=verify(out,result['events'],records)
    final=verify_final(out,result['events'])
    lines=[e['line'] for e in result['events'] if e.get('source')=='shared-ram-audit']
    negative={}
    for kind in ('kernel-aperture','wrong-owner'):
        matching=[fields(x) for x in lines if x.startswith('GPU_LOAD_POOL_MAP_REJECT ') and f'kind={kind} ' in x]
        if matching:
            if len(matching)!=1:raise ValueError('duplicate negative evidence')
            row=matching[0]
            if int(row['rc'],0)==0 or int(row['address'],0) or int(row['bytes']):raise ValueError('negative mapping succeeded')
            negative[kind]=row
        elif a.require_negative:raise ValueError('missing negative mapping: '+kind)
    remap=[fields(x) for x in lines if x.startswith('GPU_LOAD_POOL_REMAP ')]
    if a.require_negative and (len(remap)!=1 or remap[0].get('verified')!='1' or int(remap[0]['bytes'])!=LENGTH):raise ValueError('remap alias missing')
    backing=read_resource(out)
    snapshot=out/'managed-final-buffer.bin';snapshot.write_bytes(backing)
    ledger=dict(passed=True,frames=accepted['frames'],dispatches=accepted['dispatches'],
        physical_bytes=LENGTH,per_frame_guest_copy_bytes=0,verification_reads_in_batch=0,
        final_pixels=final,negative_mappings=negative,retained_contents_after_remap=remap,
        pool_lifetime='retained until VM destruction; no dynamic page reclamation claim',
        resource_snapshot_sha256=hashlib.sha256(backing).hexdigest(),
        registration_sha256=hashlib.sha256((out/'managed-pages.bin').read_bytes()).hexdigest(),
        runtime_result_sha256=hashlib.sha256((out/'result.json').read_bytes()).hexdigest(),
        scope='guest-owned managed pages, host Metal output, normal DCP presentation and native input/display recovery')
    (out/'managed-verification.json').write_text(json.dumps(ledger,indent=2)+'\n')
    print(json.dumps(ledger,indent=2))

if __name__=='__main__':main()
