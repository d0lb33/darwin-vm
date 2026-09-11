"""Multi-generation host peer for one continuously running backboardd VM.

The QEMU transport session is created once per VM and its request counter is
monotonic for the VM's life (darwin_gpu_transport.c). The guest already knows
how to resume it (driver_mmio_transport.inc, DVM_TEST_RUNNER), so a replacement
backboardd is representable without touching the device model. What this peer
adds is the bookkeeping that makes the replacement safe: an explicit generation
per guest process, a staged package handed only to the generation it was staged
for, replacement of the Metal worker at each boundary so guest-assigned handles
cannot collide, and quarantine of every imported surface a dying generation did
not explicitly retire.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import time

from driver_mmio_peer import MMIOPeer
from driver_runner_peer import capture_uart_interval

SESSION_BASE = 0x220
MAGIC_OFFSET = SESSION_BASE + 0x00
RETIRE_GENERATION = SESSION_BASE + 0x08
GUEST_GENERATION = SESSION_BASE + 0x10
GUEST_PID = SESSION_BASE + 0x18
GUEST_REVISION = SESSION_BASE + 0x20
GUEST_STATE = SESSION_BASE + 0x28
GUEST_REQUESTS = SESSION_BASE + 0x30
GUEST_IMPORTS = SESSION_BASE + 0x38
GUEST_IMPORTS_DONE = SESSION_BASE + 0x40
SESSION_MAGIC = 0x31535345534d5644
BUILTIN_REVISION = 1
# dvm_surface_registry.h: a 32-byte tombstone of session[16], id, magic.
SURFACE_RETIRED_MAGIC = 0x31544552564d44
STATES = {0: 'absent', 1: 'starting', 2: 'running', 3: 'retiring', 4: 'retired'}


class SessionPeer(MMIOPeer):
    runner = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.jobs = self.out/'session-jobs'
        self.jobs.mkdir()
        self.generation = 0
        self.generations = []
        self.staged = None
        self.staged_history = []
        self.quarantine = []
        self.worker_generations = [dict(generation=0, pid=self.proc.pid, path=str(self.worker))]
        self.reuse = True
        self.reuse_stopped = None
        self.ownership_failure = None
        self.tombstones = []
        self.errors_scanned = 0
        self.host_errors = []

    def scan_host_errors(self):
        """Sequence numbers of rejected requests, scanned incrementally; the
        old per-status list comprehension over every record was O(session)."""
        while self.errors_scanned < len(self.records):
            record = self.records[self.errors_scanned]
            self.errors_scanned += 1
            if not record['reply'].get('ok'):
                self.host_errors.append(record['seq'])
        return self.host_errors

    def pump(self):
        super().pump()
        # darwin_gpu_transport_init memsets the whole owned RAM file, so the
        # control word can only be published once QEMU has taken the session.
        if self.sock is not None and self.word(MAGIC_OFFSET) != SESSION_MAGIC:
            self.publish(MAGIC_OFFSET, SESSION_MAGIC)

    # ---- shared-RAM session block -------------------------------------
    def publish(self, offset, value):
        struct.pack_into('<Q', self.ram, offset, value)

    def word(self, offset):
        return struct.unpack_from('<Q', self.ram, offset)[0]

    def guest_status(self):
        return dict(generation=self.word(GUEST_GENERATION), pid=self.word(GUEST_PID),
                    revision=self.word(GUEST_REVISION), state=STATES.get(self.word(GUEST_STATE), 'unknown'),
                    requests=self.word(GUEST_REQUESTS), imports=self.word(GUEST_IMPORTS),
                    imports_retired=self.word(GUEST_IMPORTS_DONE),
                    retire_requested=self.word(RETIRE_GENERATION))

    def current(self):
        return self.generations[-1] if self.generations else None

    # ---- staging -------------------------------------------------------
    def stage(self, bundle, revision, worker=None, note=''):
        if not self.reuse:
            raise ValueError('session reuse stopped: '+str(self.reuse_stopped))
        bundle = Path(bundle).resolve()
        subprocess.run(['codesign', '--verify', '--strict', str(bundle)], check=True)
        signature = subprocess.run(['codesign', '-d', '-vvv', str(bundle)],
                                   capture_output=True, text=True, check=True).stderr
        payload = (bundle/'DVMProxy').read_bytes()
        info = (bundle/'Info.plist').read_bytes()
        resources = (bundle/'_CodeSignature/CodeResources').read_bytes()
        if not 0 < len(payload) <= 8*1024*1024 or len(info)+len(resources) > 16384:
            raise ValueError('driver package extent')
        job = int(time.time_ns()//1000)
        record = dict(job=job, revision=int(revision), bundle=str(bundle), note=note,
                      sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload),
                      info_sha256=hashlib.sha256(info).hexdigest(),
                      resources_sha256=hashlib.sha256(resources).hexdigest(),
                      signature=signature, worker=str(Path(worker).resolve()) if worker else None,
                      staged_monotonic=time.monotonic(), staged_unix=time.time(),
                      claimed_by=None, loaded=None,
                      scope='host signature verification only; guest loading is untested until a generation reports it')
        self.staged = dict(record, payload=payload, info=info, resources=resources)
        self.staged_history.append(record)
        (self.jobs/f'{job}-staged.json').write_text(json.dumps(record, indent=2)+'\n')
        return record

    # ---- retirement ----------------------------------------------------
    def request_retire(self, generation=None):
        if not self.reuse:
            raise ValueError('session reuse stopped: '+str(self.reuse_stopped))
        generation = generation or self.generation
        if not generation or generation != self.generation:
            raise ValueError('only the live generation can be retired')
        entry = self.current()
        if entry.get('retire_requested_monotonic'):
            raise ValueError('retirement already requested for this generation')
        entry['retire_requested_monotonic'] = time.monotonic()
        self.publish(RETIRE_GENERATION, generation)
        return entry

    def replace_worker(self, worker=None):
        """A fresh backend process. Guest-assigned handles restart at 1 in the
        replacement process, so the previous generation's live Metal objects
        must not survive into it. Killing the worker also destroys its imported
        page aliases without writing tombstones, which is why every surface the
        dying generation did not retire is quarantined rather than reused."""
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.proc.stdout.close()
        path = Path(worker or self.worker)
        self.proc = subprocess.Popen([str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.log, env=self.worker_env)
        length, = struct.unpack('<I', self.read(4))
        if not 0 < length <= 4096:
            raise ValueError('worker bootstrap extent')
        bootstrap = json.loads(self.read(length))
        if bootstrap.get('bootstrap') != 1 or bootstrap.get('queue') is not True:
            raise ValueError('worker bootstrap')
        # The device READY packet is accepted only before the first request
        # (darwin_gpu_transport.c notify_read); a mid-VM worker must not resend
        # it, and the device keeps the readiness it already has.
        record = dict(generation=self.generation, pid=self.proc.pid, path=str(path),
                      sha256=hashlib.sha256(path.read_bytes()).hexdigest(), bootstrap=bootstrap,
                      replaced_monotonic=time.monotonic())
        self.worker_generations.append(record)
        return record

    # ---- transport control ops ----------------------------------------
    def control_reply(self, request):
        op = request.get('op')
        if op == 'sessionHello':
            return self.hello(request)
        if op == 'runnerNext':
            return self.next_package()
        if op == 'runnerFetch':
            return self.fetch(request)
        if op == 'sessionQuiesce':
            return self.quiesce(request)
        if op == 'sessionStaged':
            return self.staged_result(request)
        if op == 'sessionRetire':
            return self.retired(request)
        return None

    def hello(self, request):
        pid = request.get('pid')
        if not isinstance(pid, int) or pid <= 0:
            raise ValueError('session hello pid')
        previous = self.current()
        if previous and not previous.get('retired_monotonic'):
            # An unacknowledged replacement is not a completion fence. The
            # previous process's registered compositor pages stay pinned, its
            # registry ownership was never relinquished, and no cycle may be
            # claimed from here.
            previous['unclean_exit'] = True
            previous.setdefault('last_record', len(self.records))
            previous.setdefault('last_audit', self.audit_seen)
            self.quarantine.append(dict(generation=previous['generation'], pid=previous['pid'],
                                        count=None, registered=None, retired=None,
                                        reason='generation replaced without an acknowledged retirement; imported page ownership unknown'))
            self.fail_ownership('generation %d (pid %s) was replaced without an acknowledged retirement'
                                % (previous['generation'], previous['pid']))
        elif previous:
            # The worker that could alias the previous generation's pages must
            # already be gone. The successor may only talk to the backend that
            # its predecessor's quiescence created.
            if previous.get('quiesce_worker') != self.worker_generations[-1].get('pid'):
                self.fail_ownership('generation %d was not quiesced through a worker replacement'
                                    % previous['generation'])
        self.generation += 1
        entry = dict(generation=self.generation, pid=pid, revision=None, staged_job=None,
                     started_monotonic=time.monotonic(), started_unix=time.time(),
                     guest_unix=request.get('unix'), resumed_sequence=request.get('sequence'),
                     first_record=len(self.records), first_audit=self.audit_seen,
                     first_serial=(self.out/'serial.log').stat().st_size,
                     first_display=(self.out/'stderr.log').stat().st_size,
                     retire_requested_monotonic=None, retired_monotonic=None)
        self.generations.append(entry)
        (self.jobs/f'generation-{self.generation}.json').write_text(json.dumps(entry, indent=2)+'\n')
        return dict(generation=self.generation)

    def next_package(self):
        entry = self.current()
        if entry is None:
            raise ValueError('package request before session hello')
        if self.staged is None or self.staged['claimed_by'] is not None:
            return dict(action='idle')
        self.staged['claimed_by'] = entry['generation']
        for record in self.staged_history:
            if record['job'] == self.staged['job']:
                record['claimed_by'] = entry['generation']
        entry['staged_job'] = self.staged['job']
        return dict(action='stage', job=self.staged['job'], bytes=len(self.staged['payload']),
                    sha256=self.staged['sha256'], revision=self.staged['revision'], mode='data',
                    info=base64.b64encode(self.staged['info']).decode(),
                    resources=base64.b64encode(self.staged['resources']).decode())

    def fetch(self, request):
        staged = self.staged
        if not staged or request.get('job') != staged['job'] or staged['claimed_by'] != self.generation:
            raise ValueError('fetch ownership')
        offset = request.get('offset')
        if not isinstance(offset, int) or not 0 <= offset < len(staged['payload']):
            raise ValueError('fetch bounds')
        return dict(data=base64.b64encode(staged['payload'][offset:offset+32768]).decode())

    def staged_result(self, request):
        entry = self.current()
        if not entry or request.get('generation') != entry['generation']:
            raise ValueError('staged result ownership')
        result = dict(job=request.get('job'), loaded=bool(request.get('loaded')),
                      reason=request.get('reason'), revision=request.get('revision'),
                      device_class=request.get('class'), pid=request.get('pid'),
                      sha256=request.get('sha256'), stage_root=request.get('stageRoot'),
                      monotonic=time.monotonic())
        entry['staged_result'] = result
        if self.staged and self.staged['job'] == result['job']:
            self.staged['loaded'] = result['loaded']
            for record in self.staged_history:
                if record['job'] == result['job']:
                    record['loaded'] = result['loaded']
                    record['reason'] = result['reason']
        (self.jobs/f"generation-{entry['generation']}-staged-result.json").write_text(json.dumps(result, indent=2)+'\n')
        return {}

    def retired(self, request):
        entry = self.current()
        if not entry or request.get('generation') != entry['generation']:
            raise ValueError('retirement ownership')
        if not entry.get('quiesced_monotonic'):
            raise ValueError('retirement without a quiescence handshake')
        imports = int(request.get('imports') or 0)
        retired = int(request.get('importsRetired') or 0)
        entry.update(retired_monotonic=time.monotonic(), retired_unix=time.time(),
                     revision=request.get('revision'), guest_requests=request.get('requests'),
                     relinquished=bool(request.get('relinquished')),
                     imports=imports, imports_retired=retired, imports_quarantined=imports-retired,
                     last_record=len(self.records), last_audit=self.audit_seen)
        if not entry['relinquished']:
            self.fail_ownership('generation %d did not relinquish registry ownership' % entry['generation'])
        if imports > retired:
            # Never recycle a mapping because its process disappeared. These
            # pages stay pinned in the registry for the VM's life.
            self.quarantine.append(dict(generation=entry['generation'], pid=entry['pid'],
                                        count=imports-retired, registered=imports, retired=retired,
                                        reason='process exited holding registered compositor pages'))
        (self.jobs/f"generation-{entry['generation']}-retired.json").write_text(json.dumps(entry, indent=2)+'\n')
        return {}

    # ---- evidence ------------------------------------------------------
    def generation_evidence(self, entry, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        last = entry.get('last_record', len(self.records))
        records = [r for r in self.records[entry['first_record']:last] if not str(r['op']).startswith(('runner', 'session'))]
        (directory/'driver-host.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
        audits = [json.loads(x) for x in (self.out/'driver-audit.jsonl').read_text().splitlines()] \
            if (self.out/'driver-audit.jsonl').exists() else []
        audits = [x for x in audits if x['seq'] > entry['first_audit'] and x['seq'] <= entry.get('last_audit', 1 << 62)]
        (directory/'driver-audit.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in audits))
        uart, interval = capture_uart_interval(self.out/'serial.log', entry['first_serial'])
        (directory/'guest-loading.log').write_bytes(uart)
        (directory/'guest-loading-interval.json').write_text(json.dumps(interval, indent=2)+'\n')
        display, _ = capture_uart_interval(self.out/'stderr.log', entry['first_display'], limit=64*1024*1024)
        (directory/'display.log').write_bytes(display)
        summary = dict(entry, host_requests=len(records),
                       render_submissions=sum(1 for r in records if r['reply'].get('ok') and
                                              (r['reply'].get('renderPasses') or (r['op'] == 'renderSubmit' and r['reply'].get('passes')))),
                       host_errors=[r for r in records if not r['reply'].get('ok')],
                       audit_lines=[x['line'] for x in audits])
        (directory/'generation.json').write_text(json.dumps(summary, indent=2)+'\n')
        return summary

    def stop_reuse(self, reason):
        self.reuse = False
        if self.reuse_stopped is None:
            self.reuse_stopped = reason

    def fail_ownership(self, reason):
        self.stop_reuse(reason)
        if self.ownership_failure is None:
            self.ownership_failure = reason

    def registry_directory(self):
        return Path(str(self.out/'managed-pages.bin')+'.imports')

    def write_tombstone(self, resource):
        """Authorize one kernel unpin, only after the aliasing worker is gone.

        dvm_surface_registry.h requires host mappings to disappear before the
        tombstone. Replacing the worker process establishes that more strongly
        than an in-process munmap could, and the guest still has to ask the
        kernel to unpin: nothing is recycled because a process died."""
        if not isinstance(resource, int) or not 0 < resource <= 0xffffffff:
            raise ValueError('tombstone resource identity')
        directory = self.registry_directory()
        if not (directory/('%016x.pages' % resource)).is_file():
            return False
        name = directory/('%016x.retired' % resource)
        record = bytes(self.header)+struct.pack('<QQ', resource, SURFACE_RETIRED_MAGIC)
        try:
            handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            return True
        try:
            written = os.write(handle, record)
        finally:
            os.close(handle)
        if written != len(record):
            name.unlink()
            return False
        self.tombstones.append(dict(resource=resource, generation=self.generation, monotonic=time.monotonic()))
        return True

    def quiesce(self, request):
        entry = self.current()
        if not entry or request.get('generation') != entry['generation']:
            raise ValueError('quiesce ownership')
        if entry.get('quiesced_monotonic'):
            raise ValueError('generation already quiesced')
        requested = request.get('imports') or []
        if not isinstance(requested, list) or len(requested) > 64 or \
                any(not isinstance(x, int) or not 0 < x <= 0xffffffff for x in requested):
            raise ValueError('quiesce import identities')
        # Holding the transport is the GPU quiescence point: host work is
        # synchronous inside a request, so no command buffer is in flight.
        # A failed replacement authorizes nothing: no tombstone is written and
        # the generation stays unquiesced, so no successor is accepted.
        worker = self.replace_worker()
        tombstones = [resource for resource in requested if self.write_tombstone(resource)]
        entry.update(quiesce_requested=requested, tombstones=tombstones,
                     quiesced_monotonic=time.monotonic(), quiesce_worker=worker['pid'])
        return dict(tombstones=tombstones, worker=worker['pid'])

    def revision_contract(self, entry):
        """Did this generation actually run the binary the host staged?

        A registration is not a revision change: the guest falls back to the
        boot-trusted driver when staging fails, so success has to name the
        staged revision, its package digest and its distinct class."""
        job = entry.get('staged_job')
        if job is None:
            return True, 'no package was staged for this generation'
        staged = next((s for s in self.staged_history if s['job'] == job), None)
        if staged is None:
            return False, 'claimed job %s is not in the staging history' % job
        result = entry.get('staged_result')
        if not result:
            return False, 'generation never reported a staged result'
        if not result.get('loaded'):
            return False, 'staged package was not loaded: '+str(result.get('reason'))
        if result.get('revision') != staged['revision']:
            return False, 'loaded revision %s is not the staged %s' % (result.get('revision'), staged['revision'])
        if result.get('sha256') != staged['sha256']:
            return False, 'loaded package digest %s is not the staged %s' % (result.get('sha256'), staged['sha256'])
        expected = 'DVMRevision%d' % staged['revision']
        if not str(result.get('device_class') or '').startswith(expected):
            return False, 'registered class %s does not carry %s' % (result.get('device_class'), expected)
        guest = self.guest_status()
        if guest['revision'] != staged['revision']:
            return False, 'live guest revision %s is not the staged %s' % (guest['revision'], staged['revision'])
        return True, 'revision %d, class %s, digest %s' % (staged['revision'], result['device_class'], staged['sha256'])

    def status(self):
        return dict(generation=self.generation, guest=self.guest_status(),
                    reuse=self.reuse, reuse_stopped=self.reuse_stopped,
                    ownership_failure=self.ownership_failure, tombstones=self.tombstones,
                    generations=[{k: v for k, v in g.items() if k != 'payload'} for g in self.generations],
                    staged=[{k: v for k, v in s.items()} for s in self.staged_history],
                    workers=self.worker_generations, quarantine=self.quarantine,
                    host_requests=len(self.records),
                    host_errors=list(self.scan_host_errors()))
