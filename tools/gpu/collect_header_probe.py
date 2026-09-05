#!/usr/bin/env python3
"""Read-only correlation of the single-read diagnostic; never a latency benchmark."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zlib


def collect(run):
    report=json.loads((run/'result.json').read_text())
    assert report.get('passed') and report['aux_header_only'] and not report['aux_latency'], 'run did not pass'
    assert not report['ram_restored'] and not report['debugger'] and not report['kept_paused']
    assert report['global_deadline_seconds']==120
    manifest_hash=hashlib.sha256((run/'source-manifest.json').read_bytes()).hexdigest()
    assert report['source_manifest_sha256']==report['expected_manifest_sha256']==manifest_hash
    records=report['events']
    stages=[e for e in records if re.match(r'GPU_LOAD_AUX_STAGE stage=[1-4] ',e['line'])]
    assert [int(re.search(r'stage=(\d)',e['line'])[1]) for e in stages]==[1,2,3,4]
    headers=[e['line'] for e in records if e['line'].startswith('GPU_LOAD_AUX_HEADER_ONLY ')]
    assert len(headers)==1
    crc=re.fullmatch(r'GPU_LOAD_AUX_HEADER_ONLY pass=1 bytes=4096 crc=([0-9a-f]{8})',headers[0])
    assert crc
    with (run/'aux.raw').open('rb') as raw:
        page=raw.read(4096)
        assert page[:21]==b'DVM-AUX-TRANSPORT-v1\0' and zlib.crc32(page)==int(crc[1],16)
        assert page[64:]==bytes(4032)
        # The host's known seed is the only initialized region beyond page0.
        raw.seek(4096)
        assert raw.read(0x100000-4096)==bytes(0x100000-4096)
        seed=bytes((i*37+(i>>8)*11+19)&255 for i in range(1048576))
        assert raw.read(len(seed))==seed
        while chunk:=raw.read(1048576):assert chunk==bytes(len(chunk))
        assert raw.tell()==64*1048576
    trace=[]
    for line in (run/'stderr.log').read_text().splitlines():
        if 'AUXTRACE ' in line:trace.append(dict(part.split('=',1) for part in line.split('AUXTRACE ',1)[1].split()))
    expected=['submit','backend_enter','backend_return','data_dma','cqe','irq','head_write']
    assert [t['stage'] for t in trace]==expected,'missing or ambiguous device-side correlation'
    assert len({t['cid'] for t in trace})==1
    submit,enter,ret,dma,cqe,irq,head=trace
    assert all(submit[k]==v for k,v in dict(nsid='6',opcode='2',lba='0',nlb='1').items())
    assert enter['off']=='0' and enter['bytes']=='4096' and ret['ret']=='0'
    assert dma['ok']=='1' and cqe['status']=='0' and cqe['dma_ok']=='1'
    times=[int(t['host_ns']) for t in trace];assert times==sorted(times)
    assert any('GPU_LOAD_AUX_UC_CLOSE kr=0x0' in e['line'] for e in records)
    assert any('GPU_LOAD_COMPLETE result=pass scope=auxiliary-header-only' in e['line'] for e in records)
    launch=json.loads((run/'launch.json').read_text())
    assert launch['env']['DARWIN_ANS_AUX_TRACE']=='1'
    assert not any(x in launch['argv'] for x in ('-incoming','-S','-gdb','-loadvm'))
    return dict(run=run.name,guest_header_crc32=crc[1],bytes_checksum_verified=4096,
        all_non_header_regions_match_initial_pattern=True,guest_stages=stages,device_trace=trace,
        qemu_backend_instrumented_ms=(int(ret['host_ns'])-int(enter['host_ns']))/1e6,
        qemu_submit_to_cqe_instrumented_ms=(int(cqe['host_ns'])-int(submit['host_ns']))/1e6,
        host_serial_stage3_to4_receipt_gap_ms=round((stages[3]['seconds']-stages[2]['seconds'])*1000,3),
        scope='instrumented-header-correctness-not-latency',cross_clock_subtraction=False,
        source_manifest_sha256=manifest_hash,
        source_hashes={name:hashlib.sha256((run/name).read_bytes()).hexdigest() for name in ('result.json','stderr.log','launch.json')})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('runs',nargs='+',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=dict(runs=[collect(r) for r in a.runs],latency_benchmark=False)
    with a.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
