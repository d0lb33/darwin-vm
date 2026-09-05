"""Read existing single-vCPU counters from an owned stopped QEMU in LLDB.

No expressions, function calls, register writes or memory writes. The caller
must pause the guest through QMP before attaching and detach after this read.
The layout is the native ARM64 gxfstat.c table: 2048 {pointer, u64, u64} rows.
"""
import json
from pathlib import Path
import struct
import time
import lldb


def capture(debugger, output, expected_pid):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    assert process.GetProcessID() == expected_pid
    assert process.GetState() == lldb.eStateStopped
    assert target.GetAddressByteSize() == 8
    def address(name):
        for candidate in (name, '_' + name):
            symbols = target.FindSymbols(candidate, lldb.eSymbolTypeData)
            values = {symbols.GetContextAtIndex(i).GetSymbol().GetStartAddress().GetLoadAddress(target)
                      for i in range(symbols.GetSize())}
            values.discard(lldb.LLDB_INVALID_ADDRESS)
            if len(values) == 1:
                return values.pop()
        raise RuntimeError('Missing or ambiguous symbol: ' + name)
    def memory(at, length):
        error = lldb.SBError()
        value = process.ReadMemory(at, length, error)
        if error.Fail() or len(value) != length:
            raise RuntimeError(str(error))
        return value
    # Do not infer the logical bool from a raw optimized/LTO symbol byte.
    # Require actual nonzero counts and exact histogram/aggregate agreement.
    result = dict(pid=expected_pid, captured_unix=time.time(), counters={}, registers={})
    for name in ('genter', 'gexit', 'sysreg_rd', 'sysreg_wr', 'mmio_rd', 'mmio_wr'):
        result['counters'][name] = struct.unpack('<Q', memory(address('gxfstat_' + name), 8))[0]
    for name, count in (('genter_el', 4), ('gexit_el', 4), ('sysreg_el', 4), ('exc', 64)):
        result[name] = list(struct.unpack('<' + 'Q' * count, memory(address('gxfstat_' + name), count * 8)))
    raw = memory(address('gxfstat_regtab'), 2048 * 24)
    for offset in range(0, len(raw), 24):
        pointer, reads, writes = struct.unpack_from('<QQQ', raw, offset)
        if not pointer:
            continue
        error = lldb.SBError()
        name = process.ReadCStringFromMemory(pointer, 128, error)
        if error.Fail() or not name:
            raise RuntimeError('Invalid register name')
        row = result['registers'].setdefault(name, dict(reads=0, writes=0))
        row['reads'] += reads
        row['writes'] += writes
    assert sum(x['reads'] for x in result['registers'].values()) == result['counters']['sysreg_rd']
    assert sum(x['writes'] for x in result['registers'].values()) == result['counters']['sysreg_wr']
    assert result['counters']['sysreg_rd'] > 0
    with Path(output).open('x') as out:
        json.dump(result, out, indent=2)
    print('Read-only GXF counter snapshot:', output)
