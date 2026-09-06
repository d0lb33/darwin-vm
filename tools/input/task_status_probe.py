"""Read native Mach basic info for an owned helper PID from launchd.

24A5430a exports: task_name_for_pid 0x237ccfca8, task_info 0x237cd2c44.
MACH_TASK_BASIC_INFO=20, 12 natural_t words (mach/task_info.h).
Uses native permission checks, restores the calling thread, retains no ports.
"""
import struct
import bootstrap_helper as bootstrap
import lldb


def install(debugger, slide, pid, boost=False):
    if pid <= 1:
        raise ValueError('expected an independently verified helper PID')
    calls = bootstrap.calls
    def steps():
        def base(s):
            address = s['results'][0]
            if not 0x1000 <= address < 1 << 43:
                raise RuntimeError('mmap failed')
            return address
        def own(f):
            return calls.u64(f.GetThread().GetProcess(), 0x26fd74078+slide) & 0xffffffff
        def info(f, s):
            if s['results'][-1]:
                raise RuntimeError('task_name_for_pid failed')
            p = f.GetThread().GetProcess()
            s['port'] = calls.u64(p, base(s)) & 0xffffffff
            error = lldb.SBError()
            if p.WriteMemory(base(s)+8, struct.pack('<I',12), error) != 4:
                raise RuntimeError('count write failed')
            return 0x237cd2c44, dict(x0=s['port'], x1=20,
                                    x2=base(s)+0x100,x3=base(s)+8)
        def report(f, s):
            p = f.GetThread().GetProcess()
            raw = calls.read(p, base(s)+0x100,48)
            print('HELPER_TASK_INFO pid=%d result=%s words=%s' %
                  (pid, s['results'][-1], struct.unpack('<12I',raw)), flush=True)
            return 0x237cd3d24, dict(x0=own(f),x1=s['port'])
        steps = [(0x237cd5d8c,dict(x0=0,x1=0x4000,x2=3,x3=0x1002,
                                 x4=0xffffffffffffffff,x5=0)),
                lambda f,s:(0x18c3738e0,dict(x0=base(s),x1=0x4000)),
                lambda f,s:(0x237ccfca8,dict(x0=own(f),x1=pid,x2=base(s))),
                info,report,
                lambda f,s:(0x237cd6344,dict(x0=base(s),x1=0x4000))]
        if boost:
            # Explicit diagnostic via BSD permission checks; PRIO_PROCESS=0.
            # Never writes scheduler queues or edits executable guest memory.
            steps += [(0x237cd46e8,dict(x0=0,x1=pid,x2=0xffffffec)),
                      (0x237ce3a20,dict(x0=0,x1=pid))]
        return steps
    bootstrap.steps = steps
    bootstrap.install(debugger,slide)
