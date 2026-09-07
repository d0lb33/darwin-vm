"""Additional region-transfer evidence; CARenderer GPU acceptance is separate."""
import argparse
import base64
import json
from pathlib import Path
from verify_runner_job import verify


def verify_regions(directory):
    directory=Path(directory);render=verify(directory)
    audits=[json.loads(x)['line'] for x in (directory/'driver-audit.jsonl').read_text().splitlines()]
    witness='GPU_LOAD_TEXTURE_REGIONS width=8 height=6 x=3 y=2 width_region=2 height_region=3 bad_pixels=0 padding_preserved=1'
    if audits.count(witness)!=1:raise ValueError('missing guest region/padding witness')
    records=[json.loads(x) for x in (directory/'driver-host.jsonl').read_text().splitlines()]
    allocations=[r for r in records if r['op']=='texture' and r['request'].get('width')==8 and r['request'].get('height')==6 and r['request'].get('format')==80]
    if len(allocations)!=1:raise ValueError('region texture allocation')
    handle=allocations[0]['reply']['handle']
    reads=[r for r in records if r['op']=='read' and r['request'].get('texture')==handle]
    uploads=[r for r in records if r['op']=='upload' and r['request'].get('texture')==handle]
    if len(reads)!=2 or len(uploads)!=1 or uploads[0]['seq']>=reads[0]['seq']:raise ValueError('region upload/read order')
    expected=b''.join((0xff00ff00 if 3<=x<5 and 2<=y<5 else 0xffff0000).to_bytes(4,'little') for y in range(6) for x in range(8))
    for r in reads:
        if r['reply']['row']!=32 or base64.b64decode(r['reply']['data'],validate=True)!=expected:
            raise ValueError('independent region texture pixels')
    return dict(verified=True,job=render['job'],guest_pid=render['guest_pid'],render=render,
        region=dict(width=8,height=6,bytes=192,partial_origin=[3,2],partial_extent=[2,3],row_padding_verified_by_guest=True),
        scope='exact guest 2D public region transfers plus separate CARenderer GPU control; GPU-write preservation and 3D/linear variants additionally tested on host')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('job',type=Path);a=p.parse_args()
    print(json.dumps(verify_regions(a.job),indent=2))
