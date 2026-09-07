"""One isolated VM, sequential fresh guest processes and signed driver staging."""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import time

from driver_mmio_peer import MMIOPeer


def capture_uart_interval(path, start, limit=4*1024*1024):
    # The UART file is still growing. A second read after EOF can observe a
    # newly appended byte and falsely report overflow, even for a tiny log.
    end = path.stat().st_size
    size = end-start
    if not 0 <= size <= limit:
        raise ValueError('job UART evidence exceeds bound or was truncated')
    with path.open('rb') as serial:
        serial.seek(start)
        uart = serial.read(size)
    if len(uart) != size:
        raise ValueError('job UART evidence truncated while reading')
    return uart, dict(start=start, end=end, bytes=size)


class RunnerPeer(MMIOPeer):
    runner = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.inbox = self.out/'runner-inbox'
        self.inbox.mkdir()
        self.jobs = self.out/'runner-jobs'
        self.jobs.mkdir()
        self.current = None
        self.results = []
        self.pending = None

    def control_reply(self, request):
        op = request.get('op')
        if op == 'runnerNext':
            if self.current is not None:
                raise ValueError('job still owns runner')
            files = sorted(self.inbox.glob('*.json'))
            if not files:
                return dict(action='stop' if (self.inbox/'stop').exists() else 'idle')
            path = files[0]
            job = json.loads(path.read_text())
            if not isinstance(job.get('job'), int) or not 0 < job['job'] < 2**63:
                raise ValueError('job identity')
            source = Path(job['bundle'])
            payload = (source/'DVMProxy').read_bytes()
            info = (source/'Info.plist').read_bytes()
            resources = (source/'_CodeSignature/CodeResources').read_bytes()
            if not 0 < len(payload) <= 8*1024*1024 or len(info)+len(resources)>16384:
                raise ValueError('driver package extent')
            if hashlib.sha256(payload).hexdigest() != job['sha256']:
                raise ValueError('staging input changed')
            directory = self.jobs/str(job['job'])
            directory.mkdir()
            # Per-job backend process prevents a failed child's live handles
            # from contaminating the next job and permits host-only revisions.
            self.proc.stdin.close()
            try:self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill();self.proc.wait()
            self.proc.stdout.close()
            worker=Path(job.get('worker',self.worker))
            self.proc=subprocess.Popen([str(worker)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                       stderr=self.log,env=self.worker_env)
            n,=struct.unpack('<I',self.read(4))
            if not 0<n<=4096:raise ValueError('worker bootstrap extent')
            bootstrap=json.loads(self.read(n))
            if bootstrap.get('bootstrap')!=1 or bootstrap.get('queue') is not True:
                raise ValueError('worker bootstrap')
            (directory/'worker.json').write_text(json.dumps(dict(path=str(worker),pid=self.proc.pid,
                sha256=hashlib.sha256(worker.read_bytes()).hexdigest(),bootstrap=bootstrap),indent=2)+'\n')
            shutil.copytree(source,directory/'DVMProxy.bundle')
            (directory/'job.json').write_text(json.dumps(job,indent=2)+'\n')
            path.rename(directory/'queued.json')
            self.current = dict(job=job, payload=payload, out=directory,
                                first_record=len(self.records), first_audit=self.audit_seen,
                                started=time.monotonic(),first_serial=(self.out/'serial.log').stat().st_size)
            return dict(action='stage',job=job['job'],bytes=len(payload),sha256=job['sha256'],test=job.get('test','builtin'),mode=job.get('mode','data'),development=job.get('development',False),
                        info=base64.b64encode(info).decode(),resources=base64.b64encode(resources).decode())
        if op == 'runnerFetch':
            c = self.current
            if not c or request.get('job') != c['job']['job']:
                raise ValueError('fetch ownership')
            offset = request.get('offset')
            if not isinstance(offset,int) or not 0 <= offset < len(c['payload']):
                raise ValueError('fetch bounds')
            return dict(data=base64.b64encode(c['payload'][offset:offset+32768]).decode())
        if op == 'runnerResult':
            c = self.current
            if not c or request.get('job') != c['job']['job']:
                raise ValueError('result ownership')
            self.pending = dict(c, result=request, ended=time.monotonic())
            self.current = None
            return {}
        return None

    def audit(self):
        lines = super().audit()
        if self.pending:
            c = self.pending
            records=[r for r in self.records[c['first_record']:] if not r['op'].startswith('runner')]
            audits=[json.loads(x) for x in (self.out/'driver-audit.jsonl').read_text().splitlines()]
            audits=[x for x in audits if x['seq']>c['first_audit']]
            directory=c['out']
            # Early dyld/open failures precede the child's MMIO audit mapping.
            # Preserve the bounded UART interval as a separate evidence source.
            uart, interval = capture_uart_interval(self.out/'serial.log', c['first_serial'])
            (directory/'guest-loading.log').write_bytes(uart)
            (directory/'guest-loading-interval.json').write_text(json.dumps(interval,indent=2)+'\n')
            loading=[line for line in uart.decode(errors='replace').splitlines()
                     if line.startswith(('GPU_LOAD_','DVM_DEV_LOADER '))]
            (directory/'driver-host.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
            (directory/'driver-audit.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in audits))
            for r in records:
                if 'upload_file' in r:shutil.copyfile(self.out/r['upload_file'],directory/r['upload_file'])
            (directory/'shared-ram.bin').write_bytes(self.ram[:])
            result=dict(c['result'],host_elapsed_seconds=c['ended']-c['started'],
                        expected=c['job'].get('expected','observe'),verified=False,loading_evidence=loading)
            if result['spawn']==0 and result['exit']==0 and result['signal']==0:
                from consumer_verify import verify_records
                child=[x['line'] for x in audits]
                end=child.index('GPU_LOAD_COMPLETE result=pass scope=quartzcore-render resources=0')
                result['consumer']=verify_records(directory,child[:end+1],records,c['job'].get('frames',1),c['job'].get('scene',0))
                result['verified']=True
            else:
                result['failure_evidence']=[x['line'] for x in audits if 'ERROR' in x['line'] or 'FAULT' in x['line']]
            (directory/'result.json').write_text(json.dumps(result,indent=2)+'\n')
            self.results.append(result);self.pending=None
            (self.out/'runner-results.json').write_text(json.dumps(self.results,indent=2)+'\n')
        return lines

    def verify(self, events):
        if self.current or self.pending or not self.results:
            raise ValueError('unfinished or empty runner experiment')
        pids=[r['pid'] for r in self.results]
        if len(set(pids))!=len(pids) or any(p<=0 for p in pids):
            raise ValueError('fresh-process identity')
        if not any(r['expected']=='pass' and r['verified'] for r in self.results):
            raise ValueError('no required verified GPU workload')
        if any(r['expected']=='pass' and not r['verified'] for r in self.results):
            raise ValueError('required runner workload failed')
        return dict(scope='persistent-guest-runner',jobs=self.results,
                    distinct_guest_pids=pids,verified=True)
