#!/usr/bin/env python3
"""Bounded exact-System load trial with a continuously drained, private UART.

Uses a fresh child of a hash-pinned installed parent. Never restores old RAM,
publishes a GPU, changes guest registers/memory, or targets another VM.
"""
import argparse
import json
import os
from pathlib import Path
import select
import shutil
import socket
import struct
import subprocess
import sys
import time
import re
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import HMP, SAFE_TAG, atomic_json, sha256, verify_backing_chain, wait_for_path
from proxy_uart import ProxyUART
from proxy_uart_v2 import ReliableProxyUART
from verify_roundtrip import verify as verify_roundtrip
from aux_probe import AuxProbe
from aux_namespace_dt import properties, EXPECTED
from aux_ready import AuxReady


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest', type=Path)
    p.add_argument('--tag', required=True)
    p.add_argument('--seconds', type=int, default=600)
    p.add_argument('--keep-paused', action='store_true')
    p.add_argument('--aux-namespace', action='store_true',
        help='create an owned 64 MiB raw auxiliary namespace; needs opt-in QEMU and DT')
    p.add_argument('--aux-probe', action='store_true', help='run auxiliary bulk/response/timeout peer')
    p.add_argument('--aux-latency', action='store_true',
        help='64 normal verified requests, buffered guest stage timings; implies --aux-probe')
    p.add_argument('--aux-header-only',action='store_true',help='diagnostic: one initial4KiB read,30s no-progress and120s total')
    p.add_argument('--aux-post-boot', action='store_true',
        help='gate latency on reviewed home-screen image, input ACKs and 15-second settling')
    p.add_argument('--aux-readiness-seconds',type=int,choices=(300,450),default=300,
        help='preregistered post-boot readiness deadline; workload gets another 60 seconds')
    p.add_argument('--expected-manifest-sha256',help='required post-boot experiment baseline pin')
    p.add_argument('--aux-poll-ms', type=int, choices=(1, 5), default=5,
        help='host mailbox polling interval; 5 is the historical default')
    p.add_argument('--aux-wait-input', action='store_true',
        help='after byte completion, require the unchanged input service sync ACK')
    p.add_argument('--worker', type=Path, help='enable one-shot forwarding bridge with this host worker')
    p.add_argument('--reliable', action='store_true')
    p.add_argument('--library-cache',type=Path)
    p.add_argument('--faults',action='store_true',help='reliable mode: inject one corrupt response and drop two ACKs')
    a = p.parse_args()
    if a.aux_header_only:
        if a.seconds!=120 or a.aux_latency or a.aux_post_boot or a.aux_probe or a.worker or a.keep_paused or a.aux_wait_input:
            p.error('header-only requires --seconds120 and no other probe modes or keep-paused')
        a.aux_namespace=True
    if a.aux_readiness_seconds!=300 and not a.aux_post_boot:
        p.error('--aux-readiness-seconds requires --aux-post-boot')
    if a.aux_post_boot:a.aux_latency=True
    if a.aux_latency:
        a.aux_probe = True
        expected_seconds=a.aux_readiness_seconds+60 if a.aux_post_boot else 180
        if a.seconds != expected_seconds or a.aux_wait_input or a.keep_paused:
            p.error(f'latency mode requires --seconds {expected_seconds}, no input wait, and automatic teardown')
    if a.aux_poll_ms != 5 and not a.aux_probe:
        p.error('--aux-poll-ms requires --aux-probe')
    if a.aux_probe:
        a.aux_namespace = True
    if a.aux_wait_input and not a.aux_probe:
        p.error('--aux-wait-input requires --aux-probe')
    if a.aux_namespace and a.worker:
        p.error('auxiliary discovery cannot also own the UART forwarding bridge')
    if a.reliable and (not a.worker or not a.library_cache):
        p.error('--reliable requires --worker and --library-cache')
    if a.faults and not a.reliable:p.error('--faults requires --reliable')
    if not SAFE_TAG.fullmatch(a.tag) or len(a.tag) > 40 or not 1 <= a.seconds <= 1200:
        p.error('invalid tag or seconds (1..1200)')
    m = json.loads(a.manifest.read_text())
    if a.aux_post_boot or a.aux_header_only:
        if not a.expected_manifest_sha256 or sha256(a.manifest)!=a.expected_manifest_sha256:
            raise ValueError('post-boot experiment manifest differs from explicit baseline pin')
        for flag,value in (('-fb','1179x2556'),('-fbmode','graphics')):
            if flag not in m['qemu_argv'] or m['qemu_argv'][m['qemu_argv'].index(flag)+1]!=value:
                raise ValueError('post-boot trial requires the pinned native graphics geometry')
    if a.aux_namespace:
        dt = Path(m['qemu_argv'][m['qemu_argv'].index('-dtree')+1])
        values = [v[2] for k,v in properties(dt.read_bytes()).items()
            if k[0].endswith('/arm-io/ans') and k[1]=='namespaces']
        if len(values)!=1 or list(struct.iter_unpack('<III',values[0]))!=EXPECTED+[(8,6,0)]:
            raise ValueError('auxiliary trial requires the exact namespace DT extension')
    verify_backing_chain(m['disk']['backing_chain'])
    for name, expected in m['qemu_inputs'].items():
        if sha256(Path(name)) != expected['sha256']:
            raise RuntimeError(f'changed pinned input: {name}')
    out = Path('/tmp/dvm')/a.tag
    out.mkdir(exist_ok=False)
    for name in ('run_guest_load.py','aux_probe.py','aux_namespace_dt.py','aux_ready.py'):
        shutil.copyfile(Path(__file__).with_name(name),out/name)
    aux_peer = AuxProbe(out, latency=a.aux_latency,post_boot=a.aux_post_boot,readiness_seconds=a.aux_readiness_seconds) if (a.aux_probe or a.aux_header_only) else None
    shutil.copyfile(a.manifest,out/'source-manifest.json')
    if a.aux_namespace and not aux_peer:
        with (out/'aux.raw').open('xb') as f:
            f.truncate(64 * 1024 * 1024)
            f.write(b'DVM-AUX-TRANSPORT-v1\0' + os.urandom(32))
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2',
        '-b', m['disk']['path'], str(out/'disk.qcow2')], check=True)
    original, argv, i = m['qemu_argv'], [], 0
    while i < len(original):
        key = original[i]
        if key in ('-incoming', '-loadvm', '-monitor', '-qmp', '-gdb', '-chardev', '-serial'):
            i += 2
        elif key in ('-S', '-s'):
            i += 1
        elif key == '-drive':
            if not original[i+1].startswith('if=none,id=ans,'):
                raise ValueError('unexpected drive')
            argv += [key, f'if=none,id=ans,file={out}/disk.qcow2,format=qcow2']; i += 2
        elif key == '-display':
            argv += [key, 'none']; i += 2
        else:
            argv.append(key); i += 1
    argv += ['-monitor', f'unix:{out}/monitor.sock,server=on,wait=off',
        '-qmp', f'unix:{out}/qmp.sock,server=on,wait=off',
        '-chardev', f'socket,id=gpu_uart,path={out}/uart.sock,server=on,wait=off,logfile={out}/serial.log',
        '-serial', 'chardev:gpu_uart']
    model = m['qemu_env'].copy()
    if 'DARWIN_ANS_AUX_DRIVE' in model:
        raise ValueError('manifest must not carry an uncontrolled auxiliary backend')
    if a.aux_namespace:
        argv += ['-drive', f'if=none,id=gpu_aux,file={out}/aux.raw,format=raw']
        model['DARWIN_ANS_AUX_DRIVE'] = 'gpu_aux'
    if a.aux_header_only:model['DARWIN_ANS_AUX_TRACE']='1'
    model['DARWIN_TOUCH_EVENTS'] = str(out/'events.jsonl')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DARWIN_', 'DVM_', 'GXFSTAT_'))}
    env.update(model)
    atomic_json(out/'launch.json', dict(format='darwin-vm-qemu-launch-v1', argv=argv, env=model))
    report = dict(manifest=str(a.manifest.resolve()), ram_restored=False, debugger=False, events=[])
    report['source_manifest_sha256']=sha256(a.manifest)
    report['expected_manifest_sha256']=a.expected_manifest_sha256
    report['readiness_deadline_seconds']=a.aux_readiness_seconds
    ready=AuxReady(out,aux_peer,report) if a.aux_post_boot else None
    report['aux_header_only']=a.aux_header_only
    input_ping_sent = False
    proc, wire, bridge = None, None, None
    started, reason = time.monotonic(), 'deadline'
    header_progress=started
    report.update(host_runner_monotonic_origin=started, aux_latency=a.aux_latency,
        aux_post_boot=a.aux_post_boot,
        global_deadline_seconds=a.seconds,
        aux_poll_ms=a.aux_poll_ms, aux_peer_monotonic_origin=aux_peer.started if aux_peer else None)
    try:
        with (out/'stderr.log').open('wb') as log:
            proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)
        (out/'qemu.pid').write_text(str(proc.pid)+'\n')
        wait_for_path(out/'uart.sock', time.monotonic()+15)
        wire = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        wire.settimeout(5); wire.connect(str(out/'uart.sock')); wire.setblocking(False)
        if a.worker:
            bridge = ReliableProxyUART(a.worker.resolve(),out,a.library_cache.resolve(),a.faults) if a.reliable else ProxyUART(a.worker.resolve(), out)
            report['host_worker_sha256'] = sha256(a.worker)
            if a.reliable:report['library_cache_sha256']=sha256(a.library_cache)
        print(f'{a.tag}: own PID {proc.pid}; UART connected and continuously drained', flush=True)
        pending = b''
        aux_complete = False
        input_ack = False
        input_ready_ping_sent = False
        inventory_starts = 0
        with (out/'wire.log').open('wb') as log:
            while time.monotonic()-started < a.seconds and proc.poll() is None:
                if a.aux_header_only and time.monotonic()-header_progress>=30:
                    raise TimeoutError('initial header probe made no guest-stage progress for30seconds')
                if ready:
                    now=time.monotonic()-started
                    ready.tick(wire,now)
                    if ready.released_at is not None and not aux_peer.seen and now-ready.released_at>15:
                        raise TimeoutError('no latency request within 15 seconds of readiness release')
                if aux_peer:
                    aux_peer.pump()
                    if a.aux_latency and aux_peer.seen and len(aux_peer.seen)<64 and \
                            time.monotonic_ns()-aux_peer.last_response_ns > 10_000_000_000:
                        raise TimeoutError('latency batch made no host-visible progress for 10 seconds')
                    if not a.aux_latency and 9 in aux_peer.seen and not input_ping_sent:
                        # S is the existing input protocol's no-event sync.
                        # Check it during the deliberate auxiliary timeout;
                        # no touch/button event is sent to the guest UI.
                        packet = b'\nDVMINPUT1 900001 S 0 0 0\n'
                        if wire.send(packet) != len(packet):
                            raise RuntimeError('short input synchronization probe write')
                        report['input_sync_sent_seconds'] = time.monotonic()-started
                        input_ping_sent = True
                if bridge:
                    bridge.pump(wire)
                poll_wait=.005 if ready and ready.released_at is None else a.aux_poll_ms/1000
                if not select.select([wire], [], [], poll_wait if aux_peer else .01 if bridge else .2)[0]:
                    continue
                chunk = wire.recv(65536)
                if not chunk:
                    reason = 'UART closed'; break
                log.write(chunk); log.flush()
                if a.reliable:bridge.feed(chunk)
                lines = (pending+chunk).split(b'\n'); pending = lines.pop()[-65536:]
                for raw in lines:
                    line = raw.decode(errors='replace').strip()
                    if a.aux_header_only and 'GPU_LOAD_' in line and 'GPU_LOAD_AUX_ALIVE' not in line:
                        header_progress=time.monotonic()
                    if ready:ready.feed(line,wire,time.monotonic()-started)
                    if a.aux_latency and 'GPU_LOAD_INVENTORY ' in line:
                        # A2 observed AMFI text and this marker on one serial
                        # line. Count embedded starts as well as clean lines.
                        inventory_starts += line.count('GPU_LOAD_INVENTORY ')
                        if inventory_starts > 1:
                            raise RuntimeError('latency helper restarted')
                    if bridge:
                        bridge.line(line)
                    if any(x in line for x in ('GPU_LOAD_', 'GPU_BUNDLE_', 'DVMGPU_READY', 'DVMGPU_READER_STOPPED', 'DVMGPU_DONE', 'HARNESS_', 'DVM_INPUT_', 'panic(cpu')):
                        event = dict(seconds=round(time.monotonic()-started, 3), line=line)
                        report['events'].append(event); print(json.dumps(event), flush=True)
                    if a.aux_wait_input and 'DVM_INPUT_READY ' in line and not input_ready_ping_sent:
                        # A boot-time input helper can restart after the early
                        # sync. Test the actual ready reader with a fresh ID.
                        packet = b'\nDVMINPUT1 900002 S 0 0 0\n'
                        if wire.send(packet) != len(packet):
                            raise RuntimeError('short ready input sync write')
                        input_ready_ping_sent = True
                        report['input_ready_sync_sent_seconds'] = time.monotonic()-started
                    ack_marker = 'DVM_INPUT_ACK 900002 1' if a.aux_wait_input else 'DVM_INPUT_ACK 900001 1'
                    if ack_marker in line:
                        input_ack = True
                    if 'GPU_LOAD_COMPLETE' in line:
                        aux_complete = True
                        if not a.aux_wait_input or input_ack:
                            reason = 'guest load probe completed'
                    elif 'GPU_LOAD_ERROR' in line or 'GPU_BUNDLE_ERROR' in line or 'panic(cpu' in line:
                        reason = 'guest reported failure'
                    elif bridge and bridge.finished and 'DVM_INPUT_START' in line:
                        reason = 'guest forwarding diagnostic finished; original input start marker observed'
                    elif a.reliable and 'DVMGPU_DONE reason=' in line and 'reason=complete ' not in line:
                        reason = 'guest transport reported failure'
                    if a.aux_wait_input and aux_complete and input_ack and reason == 'deadline':
                        reason = 'guest load probe completed'
                if reason != 'deadline':
                    break
        if a.reliable:
            if not bridge.finished or bridge.close_status!=0 or 'original input start marker observed' not in reason:
                raise RuntimeError('roundtrip lacks protected successful close and original input start marker')
            report['roundtrip']=verify_roundtrip(out)
            report['passed']=True
        if a.aux_header_only:
            if reason!='guest load probe completed':raise RuntimeError('initial header completion not observed')
            rows=[e['line'] for e in report['events'] if e['line'].startswith('GPU_LOAD_AUX_HEADER_ONLY ')]
            if len(rows)!=1:raise RuntimeError('missing unique header result')
            match=re.fullmatch(r'GPU_LOAD_AUX_HEADER_ONLY pass=1 bytes=4096 crc=([0-9a-f]{8})',rows[0])
            if not match or int(match[1],16)!=zlib.crc32(os.pread(aux_peer.fd,4096,0)):
                raise RuntimeError('guest header differs from exact host page')
            for offset,length in ((0x10000,4096),(0x20000,4096),(0x30000,4096),(0x400000,1048576)):
                if os.pread(aux_peer.fd,length,offset)!=bytes(length):raise RuntimeError('header-only probe wrote workload data')
            report.update(passed=True,header_bytes_verified=4096,scope='instrumented-initial-header-read')
        if aux_peer and not a.aux_header_only:
            if reason != 'guest load probe completed' or not any(
                    'scope=auxiliary-byte-transport' in e['line'] for e in report['events']):
                raise RuntimeError('auxiliary guest completion not verified')
            if a.aux_latency:
                samples = [e for e in report['events'] if e['line'].startswith('GPU_LOAD_AUX_LAT seq=')]
                sequences = [int(e['line'].split('seq=')[1].split()[0]) for e in samples]
                if sequences != list(range(1,65)) or any(' valid=1 ' not in e['line'] for e in samples):
                    raise RuntimeError('latency workload did not verify all 64 guest replies')
            report['auxiliary'] = aux_peer.verify()
            if ready and ready.released_at is None:raise RuntimeError('missing post-boot release')
            report['input_sync_ack_observed'] = input_ack
            report['passed'] = True
    except BaseException as error:
        reason = f'{type(error).__name__}: {error}'
        raise
    finally:
        # A connected but undrained UART can block the event loop. Close it
        # before issuing monitor commands at teardown.
        if wire:
            wire.close()
        if bridge:
            bridge.close()
        if aux_peer:
            aux_peer.close()
        try:
            if proc and proc.poll() is None:
                h = HMP(out/'monitor.sock', timeout=15)
                h.command('stop')
                (out/'status.txt').write_text(h.command('info status')+'\n')
                (out/'registers.txt').write_text(h.command('info registers')+'\n')
                h.command(f'screendump {out}/final.png -f png')
        except (OSError, RuntimeError, TimeoutError) as error:
            # Teardown can race a terminating QEMU. Preserve the workload
            # verdict and this separate collection failure; still reap ours.
            report['teardown_error'] = f'{type(error).__name__}: {error}'
        finally:
            if proc and proc.poll() is None and not a.keep_paused:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(timeout=5)
            report.update(elapsed=time.monotonic()-started, stop_reason=reason,
                kept_paused=bool(proc and proc.poll() is None))
            atomic_json(out/'result.json', report)
            print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
