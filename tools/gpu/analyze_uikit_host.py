#!/usr/bin/env python3
"""Compare captured Catalyst frames; never treat them as exact iOS evidence."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('native',type=Path)
    parser.add_argument('forwarded',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    manifests=[json.loads((p/'manifest.json').read_text()) for p in (args.native,args.forwarded)]
    if any(m.get('scope')!='host-catalyst-rehearsal-not-exact-guest' or m.get('exit')!=0 for m in manifests):
        raise ValueError('requires completed host captures with explicit attribution')
    if manifests[0].get('forwarded') or not manifests[1].get('forwarded'):
        raise ValueError('requires native then forwarded captures')
    count=manifests[0]['frames']
    if count not in (1,3) or manifests[1]['frames']!=count:raise ValueError('frame count differs')
    if manifests[0].get('animate',False)!=manifests[1].get('animate',False):raise ValueError('scene sequence differs')
    frames=[]
    for index in range(count):
        data=[(p/f'gpu-frame-{index}.bgra').read_bytes() for p in (args.native,args.forwarded)]
        if any(len(d)!=320*480*4 for d in data):raise ValueError('frame extent')
        delta=[abs(a-b) for a,b in zip(*data)]
        pixels={i//4 for i,d in enumerate(delta) if d>2}
        bounds=None if not pixels else [min(i%320 for i in pixels),min(i//320 for i in pixels),
                                        max(i%320 for i in pixels)+1,max(i//320 for i in pixels)+1]
        frames.append(dict(index=index,exact_match=data[0]==data[1],channels_over_2=sum(d>2 for d in delta),
            max_error=max(delta),total_error=sum(delta),difference_bounds=bounds,
            native_sha256=hashlib.sha256(data[0]).hexdigest(),forwarded_sha256=hashlib.sha256(data[1]).hexdigest()))
    result=dict(scope='host-catalyst-pixel-comparison-only-not-guest-or-display-acceptance',
        native=str(args.native.resolve()),forwarded=str(args.forwarded.resolve()),
        native_control={k:manifests[0].get(k) for k in ('native_air_override','native_contract_override','native_query_overrides','native_pipeline_delay_us')},
        all_frames_exact=all(f['exact_match'] for f in frames),frames=frames)
    if manifests[0].get('animate'):
        result['native_middle_frame_changes']=frames[0]['native_sha256']!=frames[1]['native_sha256']
        result['forwarded_middle_frame_changes']=frames[0]['forwarded_sha256']!=frames[1]['forwarded_sha256']
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
