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
from surface_peer import SurfacePeer
from driver_peer import DriverPeer
from driver_mmio_peer import MMIOPeer


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest', type=Path)
    p.add_argument('--tag', required=True)
    p.add_argument('--seconds', type=int, default=600)
    p.add_argument('--keep-paused', action='store_true')
    p.add_argument('--probe-observe-display',action='store_true',
        help='after a standalone probe completes, require native presentation and stable input with a fresh ACK')
    p.add_argument('--driver-present',action='store_true',help='mode-3 resident blur acceptance with normal DCP frame witness')
    p.add_argument('--present-frames',type=int,default=33,help='bounded batch size, including first-use frame (2..8192)')
    p.add_argument('--present-hz',type=int,choices=(0,30,60),default=0,help='absolute guest pacing; zero is unpaced')
    p.add_argument('--driver-mmio',action='store_true',help='use shared RAM and MMIO notifications with --driver-worker')
    p.add_argument('--mmio-echo',action='store_true',help='owned 16 MiB shared RAM and dedicated MMIO echo device; no auxiliary namespace')
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
    p.add_argument('--surface-worker',type=Path,help='real Metal peer for the guest IOSurface demo')
    p.add_argument('--driver-failure-snapshot',action='store_true',help='capture complete RAM only after an owned driver failure')
    p.add_argument('--driver-worker',type=Path,help='boot-time Metal driver peer; verifies native display/HID independently afterward')
    p.add_argument('--driver-wait-display',action='store_true',help='historical benchmark mode: delay GPU readiness until display/HID are ready')
    p.add_argument('--driver-late-launch',action='store_true',help='diagnostic: allow 240 seconds for the staged 180-second launchd activation')
    p.add_argument('--surface-observe-display',action='store_true',help='after GPU completion require a native presentation and fresh native HID ping ACK')
    a = p.parse_args()
    if not 2<=a.present_frames<=8192 or ((a.present_frames!=33 or a.present_hz) and not a.driver_present):
        p.error('paced batch requires --driver-present and 2..8192 frames')
    if a.driver_present and (not a.driver_mmio or not a.driver_worker or (a.driver_worker.parent/"transport-mode.txt").read_text().strip() not in ("--mmio-present","--mmio-present-pool")):
        p.error("presentation requires the matching resident MMIO build")
    if (a.present_frames!=33 or a.present_hz) and 'DVM_PRESENT_METRICS' not in (a.driver_worker.parent/'present_layout.h').read_text():
        p.error('paced batch requires a timing-ledger capable driver build')
    if a.driver_worker and (a.driver_worker.parent/"transport-mode.txt").is_file() and (a.driver_worker.parent/"transport-mode.txt").read_text().strip() in ("--mmio-present","--mmio-present-pool") and not a.driver_present:
        p.error("resident MMIO build requires --driver-present and its display witness")
    if a.driver_mmio and (not a.driver_worker or a.aux_namespace or a.driver_failure_snapshot):
        p.error("MMIO requires driver worker without namespace or snapshot")
    if a.mmio_echo and (a.aux_namespace or a.aux_probe or a.aux_latency or a.aux_header_only or a.aux_post_boot or a.worker or a.surface_worker or a.driver_worker or a.keep_paused):
        p.error('MMIO echo requires a standalone probe, no other transport or keep-paused')
    if a.probe_observe_display and (a.worker or a.driver_worker or a.surface_worker or a.aux_probe or a.aux_latency or a.aux_header_only or a.aux_post_boot or a.aux_wait_input or a.keep_paused):
        p.error('standalone probe display observation cannot combine with another workload or keep-paused')
    if a.driver_wait_display and not a.driver_worker:p.error('display gate requires driver worker')
    driver_boot=bool(a.driver_worker and not (a.driver_wait_display or a.driver_late_launch))
    if a.driver_late_launch and (not a.driver_worker or a.seconds != 300):
        p.error('late launch requires driver worker and --seconds 300')
    if a.driver_failure_snapshot and not a.driver_worker:
        p.error('failure snapshot requires driver worker')
    if a.driver_worker:
        if not a.library_cache or a.worker or a.surface_worker or a.aux_probe or a.aux_latency or a.aux_header_only or a.aux_post_boot or a.keep_paused or a.surface_observe_display:
            p.error('driver requires library cache and automatic teardown, no other workload')
        a.aux_namespace=not a.driver_mmio
    if a.surface_observe_display and not a.surface_worker:
        p.error('surface display observation requires --surface-worker')
    if a.surface_worker:
        if not a.library_cache or a.worker or a.aux_probe or a.aux_latency or a.aux_header_only or a.aux_post_boot or a.keep_paused:
            p.error('surface worker requires library cache, automatic teardown, and no other workload')
        a.aux_namespace=True
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
    if a.aux_poll_ms != 5 and not (a.aux_probe or a.surface_worker or a.driver_worker):
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
    if (a.driver_wait_display or a.driver_present) and m.get("qemu_env", {}).get("DARWIN_INPUT_UART") != "1":
        p.error("display readiness requires explicit DARWIN_INPUT_UART=1 in the manifest")
    if driver_boot and m.get('guest_installation', {}).get('start_interval'):
        p.error('boot-time driver validation requires a RunAtLoad parent, not a delayed launch')
    if a.driver_late_launch and m.get('guest_installation', {}).get('start_interval') != 180:
        p.error('late launch requires a manifest recording the staged 180-second driver interval')
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
    if a.mmio_echo:
        with (out/'shared-ram.bin').open('xb') as shared:shared.truncate(16*1024*1024)
    for name in ('run_guest_load.py','aux_probe.py','aux_namespace_dt.py','aux_ready.py'):
        shutil.copyfile(Path(__file__).with_name(name),out/name)
    aux_peer = AuxProbe(out, latency=a.aux_latency,post_boot=a.aux_post_boot,readiness_seconds=a.aux_readiness_seconds) if (a.aux_probe or a.aux_header_only) else None
    if a.surface_worker:
        aux_peer=SurfacePeer(out,a.surface_worker,a.library_cache)
        for name in ('surface_peer.py','guest_surface_demo.m'):
            shutil.copyfile(Path(__file__).with_name(name),out/name)
    if a.driver_worker:
        peer_class=MMIOPeer if a.driver_mmio else DriverPeer
        aux_peer=peer_class(out,a.driver_worker,a.library_cache,boot=driver_boot)
        if a.driver_present and (a.present_frames!=33 or a.present_hz):
            aux_peer.present_config=(a.present_frames,a.present_hz)
            atomic_json(out/'present-config.json',dict(frames=a.present_frames,hz=a.present_hz))
        if a.driver_mmio:
            shutil.copyfile(Path(__file__).with_name("driver_mmio_peer.py"),out/"driver_mmio_peer.py")
            shutil.copyfile(Path(__file__).with_name("driver_binary.py"),out/"driver_binary.py")
            shutil.copyfile(Path(__file__).with_name("blur_peer.py"),out/"blur_peer.py")
            shutil.copyfile(Path(__file__).with_name("present_peer.py"),out/"present_peer.py")
            shutil.copyfile(Path(__file__).with_name("managed_pages.py"),out/"managed_pages.py")
            if (a.driver_worker.parent/"managed_host.h").exists():shutil.copyfile(a.driver_worker.parent/"managed_host.h",out/"managed_host.h")
            for item in a.driver_worker.parent.glob("present_*"):
                if item.suffix in (".h",".m",".inc"):shutil.copyfile(item,out/item.name)
            for item in a.driver_worker.parent.glob("blur_*"):
                if item.suffix in (".h",".m",".inc"):shutil.copyfile(item,out/item.name)
            shutil.copyfile(a.driver_worker.parent/"driver_mmio_transport.inc",out/"driver_mmio_transport.inc")
        shutil.copyfile(Path(__file__).with_name('driver_peer.py'),out/'driver_peer.py')
        # Use the sources archived by the build, never later working-tree edits.
        for name in ('driver_probe.m','driver_guest.m','driver_host.m','driver_workload.m'):
            shutil.copyfile(a.driver_worker.parent/name,out/name)
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
    if 'DARWIN_GPU_SHM_PATH' in model:raise ValueError('manifest must not carry an uncontrolled shared-RAM backend')
    if a.driver_mmio:
        argv += ['-chardev',f'socket,id=dvm_gpu_notify,path={out}/gpu-notify.sock,server=on,wait=off']
    if a.mmio_echo or a.driver_mmio:model['DARWIN_GPU_SHM_PATH']=str(out/'shared-ram.bin')
    if any(key.startswith('DARWIN_GPU_MANAGED_') for key in model):raise ValueError('uncontrolled managed backend')
    if a.driver_mmio and aux_peer.managed:
        model['DARWIN_GPU_MANAGED_RAM_PATH']=str(out/'managed-ram.bin')
        model['DARWIN_GPU_MANAGED_PAGES_PATH']=str(out/'managed-pages.bin')
    if 'DARWIN_DCP_GPU_PRESENT_DIR' in model:
        model['DARWIN_DCP_GPU_PRESENT_DIR']=str(out)
    if model.get('DARWIN_GPU_PRESENT_TRANSPORT')=='1' and not a.driver_present:
        raise ValueError('mode-3 manifest requires --driver-present')
    if a.driver_present:
        model['DARWIN_GPU_PRESENT_TRANSPORT']='1'
        model['DARWIN_DCP_GPU_PRESENT_DIR']=str(out)
    if 'DARWIN_ANS_AUX_DRIVE' in model:
        raise ValueError('manifest must not carry an uncontrolled auxiliary backend')
    if a.aux_namespace:
        argv += ['-drive', f'if=none,id=gpu_aux,file={out}/aux.raw,format=raw']
        model['DARWIN_ANS_AUX_DRIVE'] = 'gpu_aux'
    if a.aux_header_only:model['DARWIN_ANS_AUX_TRACE']='1'
    model['DARWIN_TOUCH_EVENTS'] = str(out/'events.jsonl')
    if a.surface_worker or a.driver_worker or a.probe_observe_display:
        model['DARWIN_INPUT_STATUS']=str(out/'input-status.json')
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
    driver_child_seen=False
    driver_child_exited=False
    driver_poll_seen=False
    driver_ready_seen=False
    driver_last_progress=started
    report.update(host_runner_monotonic_origin=started, aux_latency=a.aux_latency,
        driver_boot=driver_boot, driver_mmio=a.driver_mmio, driver_present=a.driver_present, mmio_echo=a.mmio_echo,
        driver_late_launch=a.driver_late_launch,
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
        audit_complete = False
        input_ack = False
        input_ready_ping_sent = False
        inventory_starts = 0
        surface_ack_baseline=None
        surface_ack_evidence=None
        surface_present_baseline=0
        probe_ready_since=None
        probe_ready_identity=None
        probe_ready_acks=0
        present_recovery_baseline=None
        with (out/'wire.log').open('wb') as log:
            while time.monotonic()-started < a.seconds and proc.poll() is None:
                if a.driver_mmio:
                    for line in aux_peer.audit():
                        event=dict(seconds=round(time.monotonic()-started,3),line=line,source='shared-ram-audit')
                        report['events'].append(event);print(json.dumps(event),flush=True)
                        driver_last_progress=time.monotonic()
                        if 'GPU_LOAD_DRIVER_READY' in line:driver_ready_seen=True
                        if 'GPU_LOAD_ERROR' in line:raise RuntimeError('shared-RAM guest failure: '+line)
                        if line in ('GPU_LOAD_COMPLETE result=pass scope=metal-driver-luma submissions=8 resources=0','GPU_LOAD_COMPLETE result=pass scope=metal-driver-blur submissions=952 resources=0',f'GPU_LOAD_COMPLETE result=pass scope=metal-driver-present submissions={a.present_frames} resources=0'):
                            audit_complete=True;aux_complete=True
                            report['driver_complete_seconds']=time.monotonic()-started
                            report['completion_source']='shared-ram-audit'
                    if audit_complete and a.driver_present:
                        if present_recovery_baseline is None:
                            present_recovery_baseline=json.loads((out/'input-status.json').read_text())
                            present_recovery_baseline['log_offset']=(out/'stderr.log').stat().st_size
                            present_recovery_baseline['host_started_ns']=time.monotonic_ns()
                            aux_peer.ready_since=None
                            atomic_json(out/'present-recovery-baseline.json',present_recovery_baseline)
                        observation=aux_peer.observe_display()
                        if observation and observation['presents']>present_recovery_baseline.get('presents',0) and observation['input_status'].get('acked',0)>present_recovery_baseline.get('acked',0):
                            import present_peer
                            witness=present_peer.observe_native_recovery(out,present_recovery_baseline['log_offset'],present_recovery_baseline['host_started_ns'])
                            if witness:
                                atomic_json(out/'post-batch-native.json',witness)
                                report['native_observation']=observation
                                atomic_json(out/'driver-display.json',observation)
                                reason='guest load probe completed';break
                    if audit_complete and not driver_boot and not a.driver_present:
                        reason='guest load probe completed';break
                if a.probe_observe_display and aux_complete:
                    try:status=json.loads((out/'input-status.json').read_text())
                    except (OSError,ValueError):status={}
                    if status.get('presents',0)>0 and status.get('guest_state')=='R':
                        identity=(status.get('guest_pid'),status.get('guest_epoch'))
                        if probe_ready_since is None or identity!=probe_ready_identity:
                            probe_ready_since=time.monotonic();probe_ready_identity=identity
                            probe_ready_acks=status.get('acked',0)
                        if time.monotonic()-probe_ready_since>=10 and status.get('acked',0)>probe_ready_acks:
                            report['native_observation']=dict(seconds=time.monotonic()-started,
                                input_status=status,stable_seconds=10,fresh_ack=True,
                                scope='native-presentation-and-helper-ready-not-home-or-gesture')
                            reason='guest load probe completed'
                            break
                    else:probe_ready_since=None
                if a.driver_failure_snapshot and driver_poll_seen and not driver_ready_seen and time.monotonic()-driver_last_progress>35:
                    raise TimeoutError('driver readiness loop stopped progressing for 35 seconds')
                if a.driver_worker and aux_peer.released_at is not None:
                    activation_deadline = max(aux_peer.released_at, started+240) if a.driver_late_launch else aux_peer.released_at
                    if not driver_ready_seen and time.monotonic()-activation_deadline>(60 if driver_boot else 30):
                        raise TimeoutError('driver did not acknowledge host readiness within activation deadline')
                    if driver_ready_seen and not aux_complete and time.monotonic()-driver_last_progress>60:
                        raise TimeoutError('driver made no guest-stage or RPC progress for 60 seconds')
                if driver_boot and not a.driver_present and aux_complete and (driver_child_exited or audit_complete):
                    observation=aux_peer.observe_display()
                    if observation:
                        report['native_observation']=observation
                        atomic_json(out/'driver-display.json',observation)
                        reason='guest load probe completed'
                        break
                if a.aux_header_only and time.monotonic()-header_progress>=30:
                    raise TimeoutError('initial header probe made no guest-stage progress for30seconds')
                if ready:
                    now=time.monotonic()-started
                    ready.tick(wire,now)
                    if ready.released_at is not None and not aux_peer.seen and now-ready.released_at>15:
                        raise TimeoutError('no latency request within 15 seconds of readiness release')
                if a.surface_observe_display and aux_complete:
                    try:
                        status=json.loads((out/'input-status.json').read_text())
                    except (OSError,ValueError):
                        status={}
                    presents=(out/'stderr.log').read_text(errors='replace').count('iomfb: presented ')
                    if surface_ack_evidence and status.get('guest_state')=='R' and presents>surface_present_baseline:
                        report['native_observation']=dict(input_status=status,presentation_observed=True,presents_before=surface_present_baseline,presents_after=presents,ping_ack=surface_ack_evidence,seconds=time.monotonic()-started,scope='native-display-and-helper-ping-not-demo-presentation')
                        reason='guest load probe completed'
                        break
                if aux_peer:
                    old_count=len(aux_peer.seen)
                    aux_peer.pump()
                    if a.driver_worker and len(aux_peer.seen)>old_count:
                        driver_last_progress=time.monotonic()
                    if a.aux_latency and aux_peer.seen and len(aux_peer.seen)<64 and \
                            time.monotonic_ns()-aux_peer.last_response_ns > 10_000_000_000:
                        raise TimeoutError('latency batch made no host-visible progress for 10 seconds')
                    if not a.surface_worker and not a.driver_worker and not a.aux_latency and 9 in aux_peer.seen and not input_ping_sent:
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
                watched=[wire]
                if a.driver_mmio and aux_peer.sock:watched.append(aux_peer.sock)
                readable=select.select(watched, [], [], .01 if a.driver_mmio else poll_wait if aux_peer else .01 if bridge else .2)[0]
                if wire not in readable:
                    continue
                chunk = wire.recv(65536)
                if not chunk:
                    reason = 'UART closed'; break
                log.write(chunk); log.flush()
                if a.reliable:bridge.feed(chunk)
                lines = (pending+chunk).split(b'\n'); pending = lines.pop()[-65536:]
                for raw in lines:
                    line = raw.decode(errors='replace').strip()
                    if a.driver_mmio and line.startswith('GPU_LOAD_') and 'GPU_LOAD_DRIVER_CHILD' not in line:
                        continue  # MMIO result authority is the checked shared audit, not boot stderr.
                    if a.surface_observe_display and aux_complete and surface_ack_baseline:
                        ack=re.search(r'DVMI2A (\d+) (\d+) Q R ',line)
                        if ack and int(ack[1])==surface_ack_baseline['epoch'] and int(ack[2])>=surface_ack_baseline['next_seq']:
                            surface_ack_evidence=dict(epoch=int(ack[1]),sequence=int(ack[2]),seconds=time.monotonic()-started)
                    if a.driver_worker and 'GPU_LOAD_DRIVER_CHILD pid=' in line:
                        driver_child_seen=True
                    if a.driver_worker and 'GPU_LOAD_DRIVER_CHILD_EXIT ' in line:
                        driver_child_exited=' exit=0 signal=0' in line
                        if aux_complete and driver_child_exited and not driver_boot:
                            reason='guest load probe completed'
                    if a.driver_worker and 'GPU_LOAD_DRIVER_POLL ' in line:
                        driver_poll_seen=True
                    if a.driver_worker and 'GPU_LOAD_DRIVER_' in line:
                        driver_last_progress=time.monotonic()
                        if 'GPU_LOAD_DRIVER_READY' in line:
                            driver_ready_seen=True
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
                    if any(x in line for x in ('GPU_LOAD_', 'GPU_BUNDLE_', 'DVMGPU_READY', 'DVMGPU_READER_STOPPED', 'DVMGPU_DONE', 'HARNESS_', 'DVM_INPUT_', 'DVMI2R ', 'DVMI2A ', 'panic(cpu')):
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
                        if a.probe_observe_display:
                            report['probe_complete_seconds']=time.monotonic()-started
                        if driver_boot:
                            report['driver_complete_seconds']=time.monotonic()-started
                            try:report['display_at_driver_completion']=json.loads((out/'input-status.json').read_text())
                            except (OSError,ValueError):report['display_at_driver_completion']=None
                        if a.surface_observe_display:
                            try:
                                surface_ack_baseline=json.loads((out/'input-status.json').read_text())
                                surface_present_baseline=(out/'stderr.log').read_text(errors='replace').count('iomfb: presented ')
                                report['native_observation_baseline']=dict(status=surface_ack_baseline,presents=surface_present_baseline)
                            except (OSError,ValueError):
                                surface_ack_baseline=None
                        elif not a.aux_wait_input or input_ack:
                            if not a.probe_observe_display and not driver_boot and (not a.driver_worker or not driver_child_seen or driver_child_exited):
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
        if a.probe_observe_display:
            if reason!='guest load probe completed' or 'native_observation' not in report:
                raise RuntimeError('standalone probe did not complete with native display/input observation')
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
        if a.surface_worker:
            rows=[e['line'] for e in report['events'] if 'GPU_LOAD_SURFACE_RUN ' in e['line']]
            if reason!='guest load probe completed' or len(rows)!=3 or any('backend=host-metal' not in row or 'verified=1' not in row for row in rows):
                raise RuntimeError('guest IOSurface oracle did not verify three GPU completions')
            demo=aux_peer.verify()
            witnesses='\n'.join(e['line'] for e in report['events'])
            air='GPU_LOAD_SURFACE_AIR sha256='+demo['air_sha256']+' bytes=2705796'
            if witnesses.count(air)!=1:
                raise RuntimeError('missing unique exact guest AIR witness')
            for record,row in zip(demo['records'],rows):
                seq=record['sequence']
                if not re.search(rf'GPU_LOAD_SURFACE_RUN seq={seq} backend=host-metal id=\d+ bytes=12288 verified=1 crc={record["crc32"]} ',row):
                    raise RuntimeError('guest surface sequence/CRC does not match host Metal output')
                if witnesses.count(f'GPU_LOAD_SURFACE_SUBMIT seq={seq} nonce={record["nonce"]}')!=1:
                    raise RuntimeError('guest submission nonce does not match host input')
            aux_peer.finish()
            report['surface_demo']=demo
            report['passed']=True
        if a.driver_worker:
            if reason!='guest load probe completed':raise RuntimeError('driver workload did not complete')
            report['driver']=aux_peer.verify(report['events']);aux_peer.finish();report['passed']=True
        if aux_peer and not a.aux_header_only and not a.surface_worker and not a.driver_worker:
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
                if a.driver_failure_snapshot and not report.get('passed'):
                    with (out/'failure-snapshot.log').open('w') as snapshot_log:
                        capture=subprocess.run([sys.executable,str(Path(__file__).with_name('snapshot_driver_failure.py')),str(out)],stdout=snapshot_log,stderr=subprocess.STDOUT,timeout=180)
                    report['failure_snapshot_returncode']=capture.returncode
        except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as error:
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
            if a.driver_present and report.get('passed'):
                try:
                    import present_peer
                    report['presentation_final']=present_peer.verify_final(out,report['events'])
                except (ValueError,OSError) as error:
                    report['passed']=False;report['presentation_error']=str(error)
            atomic_json(out/'result.json', report)
            print(json.dumps(report), flush=True)
    if report.get('presentation_error'):
        raise RuntimeError(report['presentation_error'])


if __name__ == '__main__':
    main()
