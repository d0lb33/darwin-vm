#!/usr/bin/env python3
"""Keep one backboardd GPU VM alive and drive driver-revision cycles on it.

  session_cli.py start <manifest> --session DIR --worker W --library L
  session_cli.py status --session DIR
  session_cli.py stage <revision> --session DIR --bundle B
  session_cli.py restart-backboardd --session DIR
  session_cli.py test --session DIR
  session_cli.py capture --session DIR --name N
  session_cli.py cleanup --session DIR

`start` boots the pinned package and blocks until display/input readiness or
its boot deadline; every other verb talks to that already running VM through a
file inbox, so the VM survives between tests. Boot/readiness, restart and
per-test deadlines are separate. A total session cap exists only for
`--regression`, which is the automated form.
"""
import argparse
import hashlib
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import HMP, SAFE_TAG, atomic_json, sha256, verify_backing_chain
from display_acceptance import DisplayAcceptance
from session_peer import SessionPeer

# A guest exception, a device error or a lost native completion means the
# session's ownership is no longer accountable. A bounded transport-policy
# rejection does not: the frontend or host preflight refuses a request before
# executing it, the guest gets an ordinary Metal error, and the compositor
# keeps its resources. Those are recorded and the VM stays alive.
FATAL_MARKERS = ('GPU_LOAD_SYSTEM_UNCAUGHT', 'GPU_LOAD_SYSTEM_EXCEPTION', 'panic(cpu', 'GPU_LOAD_ERROR')
RECORDED_MARKERS = ('GPU_LOAD_SYSTEM_MISSING', 'GPU_LOAD_TEXTURE_REJECT', 'GPU_LOAD_SESSION_STAGE_FAILED')
REGRESSION_CAP = 600


def verifier_interpreter():
    """An interpreter that can import the pixel verifier's dependencies.

    The session daemon may run under whichever python3 the launcher's PATH
    resolved; verify_rgha_scanout.py needs numpy and Pillow."""
    probe = 'import numpy, PIL'
    for candidate in (sys.executable, shutil.which('python3'), '/opt/miniconda3/bin/python3'):
        if candidate and subprocess.run([candidate, '-c', probe], capture_output=True).returncode == 0:
            return candidate
    return sys.executable


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


class HomeProbe:
    """One native Home press and its recovery, scoped to a generation.

    This dispatches a real HID edge pair and requires a later presentation. It
    is not an independent UI-response oracle and never claims a gesture test."""

    def __init__(self, out, monitor):
        self.out, self.monitor = Path(out), monitor
        self.before = None
        self.started = None
        self.frames = 0
        self.passed = False
        self.failure = None

    def status(self):
        return read_json(self.out/'input-status.json', {}) or {}

    def begin(self, display):
        status = self.status()
        if status.get('guest_state') != 'R':
            return False
        self.before = status
        self.started = time.monotonic()
        self.frames = display.completions
        answer = HMP(self.monitor, timeout=5).command('sendkey f5 100')
        if answer:
            self.failure = 'native Home injection failed: ' + answer
        return True

    def poll(self, display, seconds=20):
        if self.started is None or self.passed or self.failure:
            return
        status = self.status()
        errors = ('dispatch_failed', 'ack_failed', 'ack_rejected', 'timeouts', 'overflow_or_not_ready_drops')
        if any(status.get(k, 0) > self.before.get(k, 0) for k in errors):
            self.failure = 'native Home dispatch/ack failure'
            return
        self.passed = (status.get('guest_state') == 'R' and
                       status.get('guest_pid') == self.before.get('guest_pid') and
                       status.get('guest_epoch') == self.before.get('guest_epoch') and
                       status.get('dispatched', 0) >= self.before.get('dispatched', 0) + 2 and
                       not any(status.get(k, 0) for k in ('queue_len', 'inflight', 'wire_pending', 'btn_down')) and
                       display.completions > self.frames)
        if not self.passed and time.monotonic()-self.started > seconds:
            self.failure = 'native Home dispatch/recovery deadline'

    def report(self):
        return dict(scope='native Home dispatch and a subsequent presentation, not an independent UI oracle',
                    before=self.before, after=self.status(), passed=self.passed, failure=self.failure)


class Session:
    def __init__(self, args):
        self.args = args
        self.root = args.session.resolve()
        self.out = self.root/'run'
        self.inbox = self.root/'control'
        self.captures = self.root/'captures'
        self.manifest = json.loads(args.manifest.read_text())
        self.display = DisplayAcceptance()
        self.generation_frames = {}
        self.pending = None
        self.stop_reason = None
        self.ready = False
        self.ready_at = None
        self.failures = []
        self.results = []
        self.timings = []
        self.serial_offset = 0
        self.stderr_offset = 0
        self.stderr_partial = ''
        self.tail = ''
        self.errtail = ''
        # backboardd's stderr is redirected to a guest file by its launchd job,
        # so every GPU_LOAD_ line reaches the host through the audit ring, not
        # the UART. BBRELOAD_SESSION3 ran 300 s and 3,854 presentations without
        # ever matching a registration because this buffer did not exist.
        self.audit_tail = ''
        self.witnesses = set()
        # Incremental render-submission count. The previous per-call generator
        # over every record made each loop iteration O(session): PERF_HOME1
        # (2026-09-07) measured the guest-visible gap between two consecutive
        # tiny RPCs growing from 0.97 ms to 6.5 ms over 20k records while the
        # host service time stayed at 0.06 ms (docs/re/gpu-home-sluggishness-ios27.md).
        self.render_scanned = 0
        self.render_total = 0
        self.uart = None
        self.proc = None
        self.home = None
        self.started = time.monotonic()

    # ---- boot ----------------------------------------------------------
    def launch(self):
        m = self.manifest
        verify_backing_chain(m['disk']['backing_chain'])
        for name, item in m['qemu_inputs'].items():
            if sha256(Path(name)) != item['sha256']:
                raise ValueError('changed pinned input '+name)
        scope = m.get('guest_installation', {}).get('scope', '')
        if not scope.startswith('backboardd-only boot registration'):
            raise ValueError('requires owned compositor installation')
        self.root.mkdir(parents=True, exist_ok=False)
        self.out.mkdir()
        self.inbox.mkdir()
        self.captures.mkdir()
        self.peer = SessionPeer(self.out, self.args.worker, self.args.library, boot=True,
                                library_cache=self.args.library_cache)
        if not self.peer.managed:
            raise ValueError('requires current managed mode-3 worker')
        subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', m['disk']['path'],
                        str(self.out/'disk.qcow2')], check=True)
        argv = list(m['qemu_argv'])
        argv[argv.index('-drive')+1] = f'if=none,id=ans,file={self.out}/disk.qcow2,format=qcow2'
        if any(x in argv for x in ('-monitor', '-qmp', '-serial', '-chardev', '-gdb', '-s', '-S', '-incoming')):
            raise ValueError('unexpected source control endpoint')
        argv += ['-monitor', f'unix:{self.out}/monitor.sock,server=on,wait=off',
                 '-qmp', f'unix:{self.out}/qmp.sock,server=on,wait=off',
                 '-chardev', f'socket,id=gpu_uart,path={self.out}/uart.sock,server=on,wait=off,logfile={self.out}/serial.log',
                 '-serial', 'chardev:gpu_uart',
                 '-chardev', f'socket,id=dvm_gpu_notify,path={self.out}/gpu-notify.sock,server=on,wait=off']
        model = dict(m['qemu_env'])
        if any(k.startswith('DARWIN_GPU_') for k in model):
            raise ValueError('uncontrolled source GPU transport')
        model.update(DARWIN_GPU_SHM_PATH=str(self.out/'shared-ram.bin'),
                     DARWIN_GPU_MANAGED_RAM_PATH=str(self.out/'managed-ram.bin'),
                     DARWIN_GPU_MANAGED_PAGES_PATH=str(self.out/'managed-pages.bin'),
                     DARWIN_GPU_PRESENT_TRANSPORT='1', DARWIN_DCP_GPU_PRESENT_DIR=str(self.out),
                     DARWIN_INPUT_STATUS=str(self.out/'input-status.json'),
                     DARWIN_TOUCH_EVENTS=str(self.out/'events.jsonl'))
        env = {k: v for k, v in os.environ.items() if not k.startswith(('DVM_', 'DARWIN_', 'GXFSTAT_'))}
        env.update(model)
        (self.out/'launch.json').write_text(json.dumps(dict(argv=argv, env=model), indent=2)+'\n')
        (self.out/'source-manifest.json').write_bytes(self.args.manifest.read_bytes())
        self.log = (self.out/'stderr.log').open('wb')
        self.proc = subprocess.Popen(argv, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        (self.out/'qemu.pid').write_text(str(self.proc.pid)+'\n')
        self.mark('launch', 0.0)

    def mark(self, name, seconds, **extra):
        self.timings.append(dict(phase=name, seconds=round(seconds, 3), monotonic=time.monotonic(), **extra))

    # ---- pumping -------------------------------------------------------
    def drain(self):
        for line in self.peer.audit():
            print(line, flush=True)
            self.audit_tail = (self.audit_tail+line+'\n')[-262144:]
        if (self.out/'serial.log').exists():
            with (self.out/'serial.log').open('rb') as f:
                f.seek(self.serial_offset)
                chunk = f.read()
                self.serial_offset = f.tell()
            text = chunk.decode(errors='replace')
            self.tail = (self.tail+text)[-262144:]
            for line in text.splitlines():
                if 'GPU_LOAD_SYSTEM_' in line or 'GPU_LOAD_SESSION_' in line:
                    print(line, flush=True)
        with (self.out/'stderr.log').open('rb') as f:
            f.seek(self.stderr_offset)
            chunk = f.read()
            self.stderr_offset = f.tell()
        text = chunk.decode(errors='replace')
        self.errtail = (self.errtail+text)[-262144:]
        lines = (self.stderr_partial+text).split('\n')
        self.stderr_partial = lines.pop()
        for line in lines:
            self.display.feed(line)

    def contracts(self):
        """Return (fatal, recorded). Fatal stops the session."""
        recorded = []
        combined = self.tail+self.audit_tail
        if self.peer.records and not self.peer.records[-1]['reply'].get('ok', False):
            last = self.peer.records[-1]
            recorded.append('rejected compositor host request seq=%s op=%s: %s'
                            % (last['seq'], last['op'], last['reply'].get('description')))
        for marker in RECORDED_MARKERS:
            if marker in combined:
                recorded.append('bounded contract rejection: '+marker)
        if self.display.failure:
            return self.display.failure, recorded
        for marker in FATAL_MARKERS:
            if marker in combined:
                return 'failed boot/compositor contract: '+marker, recorded
        if 'iomfb: display-state failed; A408 retained, no D594' in self.errtail:
            return 'native display rejected compositor swap; completion withheld', recorded
        return None, recorded

    def render_submissions_total(self):
        """Accepted render submissions over all records so far, scanned once."""
        records = self.peer.records
        while self.render_scanned < len(records):
            record = records[self.render_scanned]
            self.render_scanned += 1
            reply = record['reply']
            if reply.get('ok') and (reply.get('renderPasses') or
                                    (record['op'] == 'renderSubmit' and reply.get('passes'))):
                self.render_total += 1
        return self.render_total

    def observe_generation(self):
        entry = self.peer.current()
        if entry and entry['generation'] not in self.generation_frames:
            self.generation_frames[entry['generation']] = dict(
                completions=self.display.completions, presentations=self.display.presentations,
                records=len(self.peer.records), render_total=self.render_submissions_total())
            self.mark('generation-%d-start' % entry['generation'],
                      time.monotonic()-self.started, pid=entry['pid'])

    def generation_counts(self):
        entry = self.peer.current()
        if not entry:
            return dict(completions=0, presentations=0, render_submissions=0)
        base = self.generation_frames.get(entry['generation'],
                                          dict(completions=0, presentations=0, records=0, render_total=0))
        return dict(completions=self.display.completions-base['completions'],
                    presentations=self.display.presentations-base['presentations'],
                    render_submissions=self.render_submissions_total()-base.get('render_total', 0))

    def registered(self):
        return 'GPU_LOAD_SYSTEM_REGISTERED' in self.tail+self.audit_tail

    # ---- status --------------------------------------------------------
    def publish_status(self):
        counts = self.generation_counts()
        state = dict(
            scope='one owned backboardd VM; generations are process replacements, never a live driver unload',
            session=str(self.root), qemu_pid=self.proc.pid if self.proc else None,
            alive=bool(self.proc and self.proc.poll() is None),
            elapsed=round(time.monotonic()-self.started, 3), ready=self.ready, ready_at=self.ready_at,
            stop_reason=self.stop_reason, failures=self.failures,
            display=dict(presentations=self.display.presentations, completions=self.display.completions,
                         pending=self.display.pending, failure=self.display.failure),
            generation_display=counts, input=read_json(self.out/'input-status.json', {}),
            pending_operation=self.pending['request'] if self.pending else None,
            operations=self.results, timings=self.timings)
        state.update(self.peer.status())
        atomic_json(self.root/'status.json', state)

    # ---- control -------------------------------------------------------
    def requests(self):
        return sorted(self.inbox.glob('*.request.json'))

    def reply(self, path, value):
        target = path.with_name(path.name.replace('.request.json', '.reply.json'))
        atomic_json(target, value)
        path.rename(path.with_suffix('.done'))

    def begin_operation(self, path):
        request = json.loads(path.read_text())
        verb = request.get('verb')
        started = time.monotonic()
        if verb == 'stage':
            record = self.peer.stage(Path(request['bundle']), request['revision'],
                                     worker=request.get('worker'), note=request.get('note', ''))
            self.mark('stage', time.monotonic()-started, revision=request['revision'], job=record['job'])
            self.results.append(dict(verb=verb, ok=True, record=record))
            self.reply(path, dict(ok=True, record=record))
            return
        if verb == 'capture':
            record = self.capture(request.get('name') or 'capture-%d' % len(list(self.captures.iterdir())))
            self.mark('capture', time.monotonic()-started, capture=record['name'])
            self.results.append(dict(verb=verb, ok=record['ok'], record=record))
            self.reply(path, dict(ok=record['ok'], record=record))
            return
        if verb == 'cleanup':
            self.stop_reason = 'cleanup requested'
            self.reply(path, dict(ok=True))
            return
        if verb in ('restart-backboardd', 'test'):
            if not self.peer.reuse:
                self.reply(path, dict(ok=False, error='session reuse stopped: '+str(self.peer.reuse_stopped)))
                return
            self.pending = dict(request=request, path=path, started=started, state='begin')
            if verb == 'restart-backboardd':
                entry = self.peer.request_retire()
                self.pending.update(generation=entry['generation'], pid=entry['pid'],
                                    quiesce_started=time.monotonic())
                print('retire requested for generation %d pid %s' % (entry['generation'], entry['pid']), flush=True)
            else:
                self.pending.update(base=self.generation_counts(), generation=self.peer.generation)
            return
        self.reply(path, dict(ok=False, error='unknown verb '+str(verb)))

    def advance_operation(self):
        pending = self.pending
        if not pending:
            return
        request = pending['request']
        verb = request['verb']
        deadline = request.get('seconds', 240)
        elapsed = time.monotonic()-pending['started']
        peer = self.peer
        if verb == 'restart-backboardd':
            entry = next(g for g in peer.generations if g['generation'] == pending['generation'])
            if pending['state'] == 'begin' and entry.get('retired_monotonic'):
                self.mark('quiesce', entry['retired_monotonic']-pending['quiesce_started'],
                          generation=entry['generation'], imports=entry.get('imports'),
                          tombstones=len(entry.get('tombstones') or []),
                          quarantined=entry.get('imports_quarantined'),
                          relinquished=entry.get('relinquished'))
                peer.generation_evidence(entry, self.root/('generation-%d' % entry['generation']))
                pending.update(imports=entry.get('imports'), tombstones=entry.get('tombstones'),
                               quarantined=entry.get('imports_quarantined'),
                               relinquished=entry.get('relinquished'),
                               state='awaiting-successor', retired_at=entry['retired_monotonic'])
            if pending['state'] == 'awaiting-successor' and peer.generation > pending['generation']:
                successor = peer.current()
                self.mark('successor-spawn', successor['started_monotonic']-pending['retired_at'],
                          pid=successor['pid'], generation=successor['generation'])
                pending['state'] = 'awaiting-registration'
            if pending['state'] == 'awaiting-registration':
                successor = peer.current()
                status = peer.guest_status()
                if status['state'] == 'running' and status['generation'] == successor['generation']:
                    # A new PID and a registration are not a revision change.
                    # Require the staged revision, its package digest and its
                    # distinct class before calling this cycle a success.
                    contract, detail = peer.revision_contract(successor)
                    expected = request.get('expect_revision')
                    if contract and expected is not None and status['revision'] != expected:
                        contract, detail = False, 'live revision %s is not the requested %s' % (status['revision'], expected)
                    if contract and expected is not None and successor.get('staged_job') is None:
                        contract, detail = False, 'successor never claimed a staged package'
                    record = dict(ok=contract, generation=successor['generation'], pid=successor['pid'],
                                  previous_pid=pending['pid'], revision=status['revision'],
                                  revision_contract=detail, staged_result=successor.get('staged_result'),
                                  quiesce=dict(imports=pending.get('imports'), tombstones=pending.get('tombstones'),
                                               quarantined=pending.get('quarantined'),
                                               relinquished=pending.get('relinquished')),
                                  seconds=round(time.monotonic()-pending['started'], 3))
                    if not contract:
                        record['error'] = 'revision contract: '+detail
                        peer.stop_reuse(record['error'])
                    self.mark('restart-total', time.monotonic()-pending['started'], **{k: record[k] for k in ('generation', 'pid', 'revision')})
                    self.finish(record)
                    return
            if elapsed > deadline:
                self.finish(dict(ok=False, error='restart deadline in state '+pending['state'],
                                 state=pending['state'], guest=peer.guest_status()))
            return
        if verb == 'test':
            if pending['generation'] != peer.generation:
                self.finish(dict(ok=False, error='generation changed under the test'))
                return
            counts = self.generation_counts()
            need = request.get('presentations', 4)
            delta = counts['completions']-pending['base']['completions']
            if request.get('home') and self.home is None and delta >= max(1, need//2):
                self.home = HomeProbe(self.out, self.out/'monitor.sock')
                if not self.home.begin(self.display):
                    self.home = None
            if self.home:
                self.home.poll(self.display)
                if self.home.failure:
                    self.finish(dict(ok=False, error=self.home.failure, input=self.home.report()))
                    return
            submissions = counts['render_submissions']-pending['base']['render_submissions']
            if delta >= need and submissions >= 1 and self.display.pending is None and \
                    (not request.get('home') or (self.home and self.home.passed)):
                record = dict(ok=True, generation=peer.generation, pid=peer.current()['pid'],
                              revision=peer.guest_status()['revision'],
                              presentations=counts['presentations']-pending['base']['presentations'],
                              completions=delta, render_submissions=submissions,
                              input=self.home.report() if self.home else None,
                              seconds=round(time.monotonic()-pending['started'], 3))
                # Capture at the instant the contract is met. A screendump
                # taken after the session has stopped is a different frame from
                # the retained A408 source and cannot verify delivery.
                if request.get('capture'):
                    record['capture'] = self.capture(request['capture'])
                    record['ok'] = bool(record['capture'].get('ok'))
                    if not record['ok']:
                        record['error'] = 'capture/pixel verification failed'
                self.mark('test', time.monotonic()-pending['started'], generation=record['generation'],
                          completions=delta, submissions=submissions)
                self.home = None
                self.finish(record)
                return
            if elapsed > deadline:
                self.home = None
                self.finish(dict(ok=False, error='test deadline', completions=delta,
                                 render_submissions=submissions, guest=peer.guest_status()))
            return

    def finish(self, record):
        pending = self.pending
        self.pending = None
        record.setdefault('verb', pending['request']['verb'])
        self.results.append(record)
        self.reply(pending['path'], record)
        if not record.get('ok'):
            self.failures.append(record)

    # ---- capture -------------------------------------------------------
    def capture(self, name):
        if not SAFE_TAG.fullmatch(name):
            return dict(ok=False, name=name, error='invalid capture name')
        directory = self.captures/name
        directory.mkdir(exist_ok=False)
        monitor = HMP(self.out/'monitor.sock', timeout=10)
        before_snapshot = read_json(self.out/'last-scanout.json', {})
        stopped = monitor.command('stop')
        answer = resumed = ''
        try:
            if stopped:
                raise RuntimeError('capture stop failed: '+stopped)
            answer = monitor.command(f'screendump "{directory}/scanout.ppm"')
            if answer:
                raise RuntimeError('capture screendump failed: '+answer)
            # Copy the committed witness group before resuming. A later stop
            # may atomically replace these paths for the next capture.
            for source in ('last-scanout.a408', 'last-scanout.rgha', 'last-scanout.bgra', 'last-scanout.json'):
                if (self.out/source).exists():
                    shutil.copyfile(self.out/source, directory/source)
        finally:
            resumed = monitor.command('cont')
        if resumed:
            raise RuntimeError('capture resume failed: '+resumed)
        record = dict(ok=True, name=name, monitor=dict(stop=stopped, screendump=answer, cont=resumed),
                      status=self.peer.status(), guest=self.peer.guest_status(),
                      display=dict(presentations=self.display.presentations, completions=self.display.completions),
                      input=read_json(self.out/'input-status.json', {}))
        snapshot = read_json(directory/'last-scanout.json', None)
        record['snapshot'] = snapshot
        witness = hashlib.sha256((directory/'last-scanout.rgha').read_bytes()).hexdigest() \
            if (directory/'last-scanout.rgha').exists() else None
        record['retained_source_sha256'] = witness
        stale = (not snapshot.get('ok') or snapshot.get('version') != 1 or
                 snapshot.get('snapshot', 0) <= before_snapshot.get('snapshot', 0)) if snapshot is not None else (
                     witness is not None and witness in self.witnesses)
        self.witnesses.add(witness) if witness else None
        (directory/'shared-ram.bin').write_bytes(self.peer.ram[:])
        entry = self.peer.current()
        if entry:
            self.peer.generation_evidence(dict(entry, last_record=len(self.peer.records),
                                               last_audit=self.peer.audit_seen), directory/'generation')
        if stale:
            record['ok'] = False
            record['scanout'] = dict(exit=None, stale_retained_source=True, sha256=witness,
                                     note='No fresh successful snapshot export. Legacy QEMU may export only '
                                          'once; rebuilt QEMU supplies a new snapshot ID at each stop.')
            atomic_json(directory/'capture.json', record)
            return record
        if snapshot is not None and snapshot.get('display_on') is False:
            record['ok'] = False
            record['scanout'] = dict(exit=None, display_blanked=True,
                                     note='Display is powered off; retained RGhA is not the visible console.')
            atomic_json(directory/'capture.json', record)
            return record
        if all((directory/n).exists() for n in ('last-scanout.a408', 'last-scanout.rgha', 'last-scanout.bgra', 'scanout.ppm')):
            verifier = str(Path(__file__).with_name('verify_rgha_scanout.py'))
            check = subprocess.run([verifier_interpreter(), verifier, str(directory)],
                                   capture_output=True, text=True)
            record['scanout'] = dict(exit=check.returncode, stdout=check.stdout[-4000:], stderr=check.stderr[-2000:])
            record['ok'] = check.returncode == 0
        else:
            record['ok'] = False
            record['scanout'] = dict(exit=None, note='no retained native scanout in this session yet')
        atomic_json(directory/'capture.json', record)
        return record

    # ---- main loop -----------------------------------------------------
    def serve(self):
        args = self.args
        self.launch()
        cap = REGRESSION_CAP if args.regression else None
        try:
            while self.proc.poll() is None and self.stop_reason is None:
                if cap and time.monotonic()-self.started > cap:
                    self.stop_reason = 'regression session cap'
                    break
                self.peer.pump()
                # The guest issues its next request about a millisecond after a
                # reply, and one iteration of drain/contracts/status below costs
                # more than that, so a compositor frame's 7 RPCs used to pay the
                # bookkeeping 7 times. Serve the burst first, then account once.
                for _ in range(64):
                    if self.peer.sock is None or not select.select([self.peer.sock], [], [], .002)[0]:
                        break
                    self.peer.pump()
                self.drain()
                self.observe_generation()
                failure, recorded = self.contracts()
                for note in recorded:
                    if note not in self.failures:
                        self.failures.append(note)
                        print('recorded: '+note, flush=True)
                if self.peer.ownership_failure and not failure:
                    failure = 'ownership contract: '+self.peer.ownership_failure
                if failure:
                    self.stop_reason = failure
                if not self.ready:
                    counts = self.generation_counts()
                    if self.home is None and self.registered() and args.home_after_presentations and \
                            self.display.reached(args.home_after_presentations):
                        self.home = HomeProbe(self.out, self.out/'monitor.sock')
                        if not self.home.begin(self.display):
                            self.home = None
                    if self.home:
                        self.home.poll(self.display)
                        if self.home.failure:
                            self.stop_reason = self.home.failure
                    if self.registered() and counts['render_submissions'] and \
                            self.display.reached(args.min_presentations) and \
                            (not args.home_after_presentations or (self.home and self.home.passed)):
                        self.ready = True
                        self.ready_at = round(time.monotonic()-self.started, 3)
                        self.mark('readiness', time.monotonic()-self.started,
                                  generation=self.peer.generation, presentations=self.display.presentations)
                        self.results.append(dict(verb='readiness', ok=True, seconds=self.ready_at,
                                                 generation=self.peer.generation,
                                                 revision=self.peer.guest_status()['revision'],
                                                 input=self.home.report() if self.home else None))
                        self.home = None
                    elif time.monotonic()-self.started > args.boot_seconds:
                        self.stop_reason = 'boot/readiness deadline'
                if self.ready:
                    if self.pending:
                        self.advance_operation()
                    else:
                        found = self.requests()
                        if found:
                            try:
                                self.begin_operation(found[0])
                            except Exception as error:
                                self.pending = None
                                record = dict(ok=False, error=repr(error))
                                self.failures.append(record)
                                self.reply(found[0], record)
                self.publish_status()
                readers = ([self.uart] if self.uart else [])+([self.peer.sock] if self.peer.sock else [])
                if self.uart is None and (self.out/'uart.sock').exists():
                    self.uart = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.uart.connect(str(self.out/'uart.sock'))
                    self.uart.setblocking(False)
                if readers:
                    ready, _, _ = select.select(readers, [], [], .02)
                    if self.uart in ready:
                        try:
                            self.uart.recv(65536)
                        except BlockingIOError:
                            pass
                else:
                    time.sleep(.02)
        except Exception as error:
            self.stop_reason = self.stop_reason or ('orchestration error: '+repr(error))
            raise
        finally:
            self.stop_reason = self.stop_reason or ('QEMU exited' if self.proc.poll() is not None else 'stopped')
            for path in self.requests():
                self.reply(path, dict(ok=False, error='session stopped: '+str(self.stop_reason)))
            self.shutdown()

    def shutdown(self):
        try:
            if self.proc and self.proc.poll() is None:
                monitor = HMP(self.out/'monitor.sock', timeout=10)
                monitor.command('stop')
                monitor.command(f'screendump "{self.out}/scanout.ppm"')
                monitor.command('quit')
                self.proc.wait(timeout=20)
        except Exception:
            pass
        if self.uart:
            self.uart.close()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=20)
        try:
            for entry in self.peer.generations:
                self.peer.generation_evidence(dict(entry, last_record=entry.get('last_record', len(self.peer.records)),
                                                   last_audit=entry.get('last_audit', self.peer.audit_seen)),
                                              self.root/('generation-%d' % entry['generation']))
        except Exception:
            pass
        self.publish_status()
        try:
            self.peer.close()
        except Exception:
            pass
        try:
            verify_backing_chain(self.manifest['disk']['backing_chain'])
        except Exception as error:
            self.failures.append(dict(verb='backing-chain', ok=False, error=repr(error)))
        self.publish_status()
        atomic_json(self.root/'final.json', json.loads((self.root/'status.json').read_text()))


def submit(session, request, seconds):
    inbox = Path(session).resolve()/'control'
    if not inbox.is_dir():
        raise SystemExit('no live session at '+str(session))
    name = '%020d' % (time.time_ns()//1000)
    path = inbox/(name+'.request.json')
    temporary = inbox/(name+'.tmp')
    temporary.write_text(json.dumps(request, indent=2)+'\n')
    os.rename(temporary, path)
    reply = inbox/(name+'.reply.json')
    deadline = time.monotonic()+seconds+30
    while time.monotonic() < deadline:
        if reply.exists():
            value = read_json(reply)
            if value is not None:
                return value
        status = read_json(Path(session).resolve()/'status.json', {}) or {}
        if not status.get('alive', True):
            raise SystemExit('session is no longer running: '+str(status.get('stop_reason')))
        time.sleep(.25)
    raise SystemExit('no reply within the operation deadline')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='verb', required=True)

    start = sub.add_parser('start')
    start.add_argument('manifest', type=Path)
    start.add_argument('--session', type=Path, required=True)
    start.add_argument('--worker', type=Path, required=True)
    start.add_argument('--library', type=Path, required=True)
    start.add_argument('--library-cache', type=Path)
    start.add_argument('--min-presentations', type=int, default=8)
    start.add_argument('--home-after-presentations', type=int, default=4)
    start.add_argument('--boot-seconds', type=int, default=300)
    start.add_argument('--regression', action='store_true', help='automated form: hard 600 s session cap')
    start.add_argument('--foreground', action='store_true')

    for name in ('status', 'cleanup'):
        q = sub.add_parser(name)
        q.add_argument('--session', type=Path, required=True)
    stage = sub.add_parser('stage')
    stage.add_argument('revision', type=int)
    stage.add_argument('--session', type=Path, required=True)
    stage.add_argument('--bundle', type=Path, required=True)
    stage.add_argument('--worker', type=Path)
    stage.add_argument('--note', default='')
    restart = sub.add_parser('restart-backboardd')
    restart.add_argument('--session', type=Path, required=True)
    restart.add_argument('--seconds', type=int, default=240)
    restart.add_argument('--worker', type=Path)
    restart.add_argument('--expect-revision', type=int,
                         help='require the replacement to run this staged revision, not a fallback registration')
    test = sub.add_parser('test')
    test.add_argument('--session', type=Path, required=True)
    test.add_argument('--presentations', type=int, default=4)
    test.add_argument('--home', action='store_true')
    test.add_argument('--seconds', type=int, default=180)
    test.add_argument('--capture', help='screendump and verify delivered pixels the moment the test passes')
    capture = sub.add_parser('capture')
    capture.add_argument('--session', type=Path, required=True)
    capture.add_argument('--name', default=None)
    serve = sub.add_parser('serve')
    serve.add_argument('manifest', type=Path)
    serve.add_argument('--session', type=Path, required=True)
    serve.add_argument('--worker', type=Path, required=True)
    serve.add_argument('--library', type=Path, required=True)
    serve.add_argument('--library-cache', type=Path)
    serve.add_argument('--min-presentations', type=int, default=8)
    serve.add_argument('--home-after-presentations', type=int, default=4)
    serve.add_argument('--boot-seconds', type=int, default=300)
    serve.add_argument('--regression', action='store_true')

    a = p.parse_args()
    if a.verb == 'serve':
        Session(a).serve()
        return
    if a.verb == 'start':
        root = a.session.resolve()
        if root.exists():
            raise SystemExit('session directory already exists: '+str(root))
        if a.foreground:
            Session(a).serve()
            return
        argv = [sys.executable, str(Path(__file__).resolve()), 'serve', str(a.manifest.resolve()),
                '--session', str(root), '--worker', str(a.worker.resolve()), '--library', str(a.library.resolve()),
                '--min-presentations', str(a.min_presentations), '--boot-seconds', str(a.boot_seconds),
                '--home-after-presentations', str(a.home_after_presentations)]
        if a.library_cache:
            argv += ['--library-cache', str(a.library_cache.resolve())]
        if a.regression:
            argv.append('--regression')
        root.parent.mkdir(parents=True, exist_ok=True)
        log = root.parent/(root.name+'.daemon.log')
        with log.open('wb') as handle:
            daemon = subprocess.Popen(argv, stdout=handle, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic()+a.boot_seconds+90
        while time.monotonic() < deadline:
            status = read_json(root/'status.json')
            if status and (status.get('ready') or status.get('stop_reason')):
                print(json.dumps(dict(daemon=daemon.pid, log=str(log), ready=status.get('ready'),
                                      ready_at=status.get('ready_at'), stop_reason=status.get('stop_reason'),
                                      generation=status.get('generation'), guest=status.get('guest')), indent=2))
                raise SystemExit(0 if status.get('ready') else 1)
            if daemon.poll() is not None:
                raise SystemExit('session daemon exited; see '+str(log))
            time.sleep(1)
        raise SystemExit('no readiness within the boot deadline; see '+str(log))
    if a.verb == 'status':
        status = read_json(a.session.resolve()/'status.json')
        if status is None:
            raise SystemExit('no session status at '+str(a.session))
        guest = status.get('guest', {})
        print(json.dumps(dict(
            session=status.get('session'), alive=status.get('alive'), qemu_pid=status.get('qemu_pid'),
            ready=status.get('ready'), elapsed=status.get('elapsed'), stop_reason=status.get('stop_reason'),
            backboardd_pid=guest.get('pid'), loaded_revision=guest.get('revision'),
            reuse=status.get('reuse'), reuse_stopped=status.get('reuse_stopped'),
            ownership_failure=status.get('ownership_failure'), tombstones=status.get('tombstones'),
            transport_generation=guest.get('generation'), guest_state=guest.get('state'),
            outstanding_requests=status.get('host_requests'), host_errors=status.get('host_errors'),
            imports=guest.get('imports'), imports_retired=guest.get('imports_retired'),
            quarantine=status.get('quarantine'), staged=status.get('staged'),
            last_display=status.get('display'), generation_display=status.get('generation_display'),
            last_input=status.get('input'), pending_operation=status.get('pending_operation'),
            operations=status.get('operations'), failures=status.get('failures')), indent=2))
        return
    if a.verb == 'stage':
        print(json.dumps(submit(a.session, dict(verb='stage', revision=a.revision,
                                                bundle=str(a.bundle.resolve()), note=a.note,
                                                worker=str(a.worker.resolve()) if a.worker else None), 60), indent=2))
        return
    if a.verb == 'restart-backboardd':
        print(json.dumps(submit(a.session, dict(verb='restart-backboardd', seconds=a.seconds,
                                                expect_revision=a.expect_revision,
                                                worker=str(a.worker.resolve()) if a.worker else None), a.seconds), indent=2))
        return
    if a.verb == 'test':
        print(json.dumps(submit(a.session, dict(verb='test', seconds=a.seconds, capture=a.capture,
                                                presentations=a.presentations, home=a.home), a.seconds), indent=2))
        return
    if a.verb == 'capture':
        print(json.dumps(submit(a.session, dict(verb='capture', name=a.name), 120), indent=2))
        return
    if a.verb == 'cleanup':
        print(json.dumps(submit(a.session, dict(verb='cleanup'), 60), indent=2))
        return


if __name__ == '__main__':
    main()
