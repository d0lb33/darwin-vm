"""Provision real files through native guest syscalls at a paused EL0 boundary.

No guest code is injected. Uses libsystem_kernel's existing mmap SVC instruction
(24A5430a static 0x237cd5e38), stopping before its error handling. Each call
restores the interrupted GP registers and flags. Intended for checkpointed,
single-vCPU development guests; never use on an uncheckpointed system.
"""
import hashlib
import json
from pathlib import Path
import lldb


class Provisioner:
    def __init__(self, debugger, slide, expected_process):
        self.debugger = debugger
        self.target = debugger.GetSelectedTarget()
        self.process = self.target.GetProcess()
        self.svc = slide + 0x237cd5e38
        self.bzero = slide + 0x18c3738e0
        self.expected_process = expected_process
        import welcome_abort_callbacks as welcome
        assert welcome._progname(self.process) == expected_process
        self.frame = self.process.GetSelectedThread().GetFrameAtIndex(0)
        assert self.reg('cpsr') & 15 == 0, 'requires paused EL0'
        error = lldb.SBError()
        assert self.process.ReadMemory(self.svc, 4, error) == bytes.fromhex('011000d4')
        self.buffer = 0
        self.capacity = 0x100000
        self.events = []

    def reg(self, name):
        return self.process.GetSelectedThread().GetFrameAtIndex(0).FindRegister(name).GetValueAsUnsigned()

    def setreg(self, name, value):
        r = self.process.GetSelectedThread().GetFrameAtIndex(0).FindRegister(name)
        assert r.SetValueFromCString(hex(value & ((1 << 64) - 1))), name

    def syscall(self, number, *args):
        import welcome_abort_callbacks as welcome
        assert welcome._progname(self.process) == self.expected_process
        names = ['x%d' % i for i in range(31)] + ['sp', 'pc', 'cpsr']
        saved = {name: self.reg(name) for name in names}
        owner = self.reg('tpidr_el1')
        bp = self.target.BreakpointCreateByAddress(self.svc + 4)
        old_async = self.debugger.GetAsync()
        self.debugger.SetAsync(False)
        try:
            for i, value in enumerate(args):
                self.setreg('x%d' % i, value)
            self.setreg('x16', number)
            self.setreg('pc', self.svc)
            error = self.process.Continue()
            assert error.Success(), str(error)
            assert self.reg('pc') == self.svc + 4, 'unexpected stop; registers NOT restored'
            assert self.reg('tpidr_el1') == owner, 'different guest thread; registers NOT restored'
            result, failed = self.reg('x0'), bool(self.reg('cpsr') & (1 << 29))
            for name, value in saved.items():
                self.setreg(name, value)
            self.events.append({'syscall': number, 'args': list(args), 'result': result, 'error': failed})
            return result, failed
        finally:
            self.target.BreakpointDelete(bp.GetID())
            self.debugger.SetAsync(old_async)

    def allocate(self):
        address, failed = self.syscall(197, 0, self.capacity, 3, 0x1002, -1, 0)
        assert not failed, ('mmap', address)
        self.buffer = address
        self.fault_buffer()
        return hex(address)

    def fault_buffer(self):
        """Native bzero faults anonymous pages in before GDB memory writes."""
        frame = self.process.GetSelectedThread().GetFrameAtIndex(0)
        names = ['x%d' % i for i in range(31)] + ['sp', 'pc', 'cpsr']
        saved = {name: self.reg(name) for name in names}
        vectors = {name: frame.FindRegister(name).GetValue()
                   for name in ['v%d' % i for i in range(32)] + ['fpsr', 'fpcr']
                   if frame.FindRegister(name).IsValid()}
        owner = self.reg('tpidr_el1')
        bp = self.target.BreakpointCreateByAddress(self.svc + 4)
        old_async = self.debugger.GetAsync()
        self.debugger.SetAsync(False)
        try:
            self.setreg('x0', self.buffer)
            self.setreg('x1', self.capacity)
            self.setreg('lr', self.svc + 4)
            self.setreg('pc', self.bzero)
            assert self.process.Continue().Success()
            assert self.reg('pc') == self.svc + 4 and self.reg('tpidr_el1') == owner
            for name, value in saved.items():
                self.setreg(name, value)
            frame = self.process.GetSelectedThread().GetFrameAtIndex(0)
            for name, value in vectors.items():
                assert frame.FindRegister(name).SetValueFromCString(value), name
        finally:
            self.target.BreakpointDelete(bp.GetID())
            self.debugger.SetAsync(old_async)

    def put(self, data):
        assert self.buffer and len(data) <= self.capacity
        error = lldb.SBError()
        assert self.process.WriteMemory(self.buffer, data, error) == len(data), str(error)
        assert self.process.ReadMemory(self.buffer, len(data), error) == data

    def mkdir(self, path):
        self.put(str(path).encode() + b'\0')
        return self.syscall(136, self.buffer, 0o755)

    def write_file(self, source, destination):
        data = Path(source).read_bytes()
        self.put(str(destination).encode() + b'\0')
        # O_WRONLY | O_CREAT | O_EXCL: refuse to overwrite an existing file.
        fd, failed = self.syscall(5, self.buffer, 0xa01, 0o644)
        assert not failed, ('open', destination, fd)
        try:
            for start in range(0, len(data), self.capacity):
                chunk = data[start:start + self.capacity]
                self.put(chunk)
                count, failed = self.syscall(4, fd, self.buffer, len(chunk))
                assert not failed and count == len(chunk), ('write', count, failed)
        finally:
            result, failed = self.syscall(6, fd)
            assert not failed, ('close', result)
        record = {'source': str(source), 'destination': str(destination),
                  'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        self.events.append(record)
        print('GUEST_FILE_PROVISIONED ' + json.dumps(record), flush=True)

    def verify_file(self, source, destination):
        expected = Path(source).read_bytes()
        self.put(str(destination).encode() + b'\0')
        fd, failed = self.syscall(5, self.buffer, 0, 0)
        assert not failed, ('open-read', fd)
        digest = hashlib.sha256()
        count = 0
        try:
            while True:
                size, failed = self.syscall(3, fd, self.buffer, self.capacity)
                assert not failed and size <= self.capacity, ('read', size, failed)
                if not size:
                    break
                error = lldb.SBError()
                data = self.process.ReadMemory(self.buffer, size, error)
                assert error.Success() and len(data) == size
                digest.update(data)
                count += size
        finally:
            assert not self.syscall(6, fd)[1]
        assert count == len(expected) and digest.hexdigest() == hashlib.sha256(expected).hexdigest()
        print('GUEST_FILE_VERIFIED %s bytes=%d sha256=%s' % (destination, count, digest.hexdigest()), flush=True)

    def save(self, path):
        Path(path).write_text(json.dumps(self.events, indent=2))

    def release(self):
        result, failed = self.syscall(73, self.buffer, self.capacity)
        assert not failed, ('munmap', result)
        self.buffer = 0
