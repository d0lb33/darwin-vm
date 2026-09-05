#!/usr/bin/env python3
"""Verify and summarize fixed 64-request auxiliary latency trials, read-only.

No VM operations. Keep each cold boot separate; no cross-clock subtraction or
population tail estimates. Output creation is exclusive to preserve evidence.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import struct
import zlib


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def distribution(values):
    return dict(min=min(values), median=statistics.median(values), max=max(values))


def collect(run):
    report = json.loads((run/'result.json').read_text())
    assert report.get('passed') and report['aux_latency'], 'trial did not pass'
    assert not report['ram_restored'] and not report['kept_paused']
    assert report['auxiliary']['live_requests_verified'] == 64
    post_boot=report.get('aux_post_boot',False)
    if post_boot:
        release=report['readiness_release']
        review=release['review']
        assert review['home_visible'] is True
        assert digest(run/review['image'])==review['image_sha256']
        readiness_seconds=report.get('readiness_deadline_seconds',300)
        assert readiness_seconds in (300,450)
        assert release['seconds']<readiness_seconds and report['global_deadline_seconds']==readiness_seconds+60
        assert report['source_manifest_sha256']==digest(run/'source-manifest.json')
        assert report['expected_manifest_sha256']==report['source_manifest_sha256']
        assert review['source_manifest_sha256']==report['source_manifest_sha256']
        assert digest(run/release['review_file'])==release['review_sha256']
        assert json.loads((run/release['review_file']).read_text())==review
        events=report['readiness_events']
        assert [e['seconds'] for e in events]==sorted(e['seconds'] for e in events)
        assert any(e['event']=='guest_wait' and e['seconds']<release['seconds'] for e in events)
        captures=[e for e in events if e['event']=='image_ready' and e['image']==review['image']]
        assert len(captures)==1
        capture=captures[0]
        assert capture['seconds']-capture['input_ack_seconds']>=15
        acks=[e for e in events if e['event']=='sync_ack' and e['kind']=='settle' and e['seconds']==capture['input_ack_seconds']]
        assert len(acks)==1
        ack=acks[0]
        assert any(e['event']=='sync_sent' and e['sequence']==ack['sequence'] and e['seconds']<=ack['seconds'] for e in events)
        approves=[e for e in events if e['event']=='image_approved' and e['review_file']==release['review_file']]
        assert len(approves)==1 and capture['seconds']<=approves[0]['seconds']<=capture['seconds']+60
        final_acks=[e for e in events if e['event']=='sync_ack' and e['kind']=='release']
        assert len(final_acks)==1 and final_acks[0]['seconds']==release['seconds']
        final_ack=final_acks[0]
        assert any(e['event']=='sync_sent' and e['sequence']==final_ack['sequence'] and approves[0]['seconds']<=e['seconds']<=final_ack['seconds'] for e in events)
        assert not any(e['event'] in ('input_start','input_ready') and e['seconds']>ack['seconds'] for e in events)
        launch=json.loads((run/'launch.json').read_text())
        assert not any(flag in launch['argv'] for flag in ('-incoming','-loadvm','-S','-gdb'))
    guest = []
    for event in report['events']:
        if event['line'].startswith('GPU_LOAD_AUX_LAT seq='):
            row = dict(item.split('=') for item in event['line'].split()[1:])
            row = {key: int(value) if key in ('seq','valid','polls','crc_retries') else float(value)
                for key,value in row.items()}
            assert row['valid'] == 1 and row['polls'] >= 1
            assert all(math.isfinite(v) and v >= 0 for v in row.values())
            assert row['total_ms'] < 5000
            components=sum(row[k] for k in ('write_ms','read_ms','sleep_ms','verify_ms'))
            assert components <= row['total_ms'] + .00001
            assert row['max_read_ms'] <= row['read_ms'] + .00001
            assert row['max_sleep_ms'] <= row['sleep_ms'] + .00001
            guest.append(dict(tag=run.name, host_poll_ms=report['aux_poll_ms'], **row))
    assert [row['seq'] for row in guest] == list(range(1,65)), 'guest sequence coverage'
    host = [json.loads(line) for line in (run/'aux-host.jsonl').read_text().splitlines()]
    assert [row['sequence'] for row in host] == list(range(1,65)), 'host sequence coverage'
    for row in host:
        assert row['request_verified'] and row['response_written'] and not row['injected_timeout']
        assert row['poll_ns'] <= row['observed_ns'] <= row['validated_ns'] <= row['handled_ns']
        assert row['host_service_ns'] == row['handled_ns']-row['observed_ns']
        assert row['host_reply_ns'] == row['handled_ns']-row['validated_ns']
        row.update(tag=run.name, host_poll_ms=report['aux_poll_ms'])
    with (run/'aux.raw').open('rb') as raw:
        header = raw.read(4096)
        assert header[:21] == b'DVM-AUX-TRANSPORT-v1\0'
        assert header[128:144] == b'DVMLAT01'+struct.pack('<II',64,1000000)
        if post_boot:
            assert header[144:152]==b'DVMWAIT1'
            budget=struct.unpack_from('<I',header,152)[0]
            assert (budget or 330)==readiness_seconds+30
            assert header[:64].hex()==review['session']
            raw.seek(0x30000);gate=raw.read(4096)
            assert gate[:72]==header[:64]+b'DVMGO001'
            assert struct.unpack_from('<I',gate,72)[0]==zlib.crc32(gate[:72])
        seed = bytes((i*37+(i>>8)*11+19)&255 for i in range(1024*1024))
        raw.seek(0x100000); assert raw.read(len(seed)) == seed, 'seed modified'
        raw.seek(0x400000); assert raw.read(len(seed)) == bytes(b^0x5a for b in seed), 'bulk mismatch'
        for offset,xor in ((0x10000,0),(0x20000,0xa5)):
            raw.seek(offset); packet=raw.read(4096)
            assert packet[:64] == header[:64] and struct.unpack_from('<I',packet,64)[0] == 64
            assert zlib.crc32(packet[72:]) == struct.unpack_from('<I',packet,68)[0]
            assert packet[72:] == bytes(((i*13+64*17)&255)^xor for i in range(72,4096))
    summary=dict(tag=run.name, host_poll_ms=report['aux_poll_ms'], samples=len(guest),post_boot=post_boot,
        readiness_deadline_seconds=report.get('readiness_deadline_seconds',300) if post_boot else None,
        guest_ms={key:distribution([row[key] for row in guest]) for key in guest[0] if key.endswith('_ms') and key!='host_poll_ms'},
        guest_polls=distribution([row['polls'] for row in guest]),
        host_ms={key:distribution([row[key]/1e6 for row in host])
            for key in ('host_service_ns','host_reply_ns','poll_gap_ns')},
        host_max_poll_gap_after_first_request_ms=max(row['max_poll_gap_ns'] for row in host[1:])/1e6,
        host_runner_cpu_percent=100*report['auxiliary']['host_runner_active_cpu_ns']/report['auxiliary']['host_active_wall_ns'],
        elapsed_seconds=report['elapsed'],
        sources={name:digest(run/name) for name in ('result.json','aux-host.jsonl','launch.json','aux_probe.py','run_guest_load.py')})
    return summary,guest,host


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs',nargs='+',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    args.output.mkdir(exist_ok=False)
    summaries,guest,host=[],[],[]
    for run in args.runs:
        summary,g,h=collect(run)
        summaries.append(summary);guest.extend(g);host.extend(h)
    assert len({r['readiness_deadline_seconds'] for r in summaries})==1,'do not mix readiness budgets'
    assert len({r['post_boot'] for r in summaries})==1,'do not mix workload release phases'
    result=dict(scope='post-home-screen-verified-byte-transport' if summaries[0]['post_boot'] else 'early-cold-boot-verified-byte-transport',runs=summaries,
        cpu_gpu_speedup_tested=False, cross_clock_subtraction=False)
    if len(summaries)==4 and [r['host_poll_ms'] for r in summaries]==[5,1,1,5]:
        pairs=[]
        for a,b in ((summaries[0],summaries[1]),(summaries[3],summaries[2])):
            baseline,candidate=a['guest_ms']['total_ms'],b['guest_ms']['total_ms']
            reduction=1-candidate['median']/baseline['median']
            pairs.append(dict(baseline=a['tag'],candidate=b['tag'],median_reduction=reduction,
                passes_gate=reduction>=.2 and candidate['max']<=2*baseline['max']))
        result.update(pairs=pairs,improvement_gate_passed=all(p['passes_gate'] for p in pairs))
    for name,rows in (('guest.csv',guest),('host.csv',host)):
        with (args.output/name).open('x') as output:
            writer=csv.DictWriter(output,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (args.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
