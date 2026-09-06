#!/usr/bin/env python3
"""Measure the native NO_IOLOG serial setting on a disposable 24A5430a replay.

Changes only the already initialized kernel flag, then closes the raw GDB
connection before timing. No breakpoints or guest execution. Persistent
validation must use native serial=19 (3 | SERIALMODE_NO_IOLOG), not this write.
See Apple XNU osfmk/console/serial_protos.h and arm/arm_init.c.
"""
import argparse
import json
import socket
from pathlib import Path
from inspect_migration_processes import DemandMemory

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--monitor', required=True)
p.add_argument('--port', type=int, required=True)
p.add_argument('--out', type=Path, required=True)
p.add_argument('--value', type=int, choices=[0, 1], required=True)
a = p.parse_args()
m = DemandMemory(Path(a.monitor), a.out / 'pages')
# The observed DART logger's authenticated import resolves to IOLog at
# 0xfffffff02b1e1060. IOLog checks this byte before serial formatting/drain,
# after retaining the ordinary unified-log call. Slide is verified by bytes.
site = 0xfffffff02b1e10f4
flag = 0xfffffff027e5a32a
if m.kernel(site, 12).hex() != 'c863feb008a94c3968020037':
    raise RuntimeError('different kernel IOLog serial gate')
before = m.kernel(flag, 1)
if before not in (b'\x00', b'\x01'):
    raise RuntimeError('serial gate is not Boolean')
pa = m.pages[flag & ~0x3fff] + (flag & 0x3fff)
with socket.create_connection(('127.0.0.1', a.port), timeout=5) as sock:
    def receive_exact(count):
        data = b''
        while len(data) < count:
            part = sock.recv(count - len(data))
            if not part:
                raise RuntimeError('closed GDB connection')
            data += part
        return data

    def packet(message):
        data = message.encode()
        sock.sendall(b'$' + data + b'#' + f'{sum(data) % 256:02x}'.encode())
        while receive_exact(1) != b'$':
            pass
        data = b''
        while True:
            ch = receive_exact(1)
            if ch == b'#':
                break
            data += ch
        if int(receive_exact(2), 16) != sum(data) % 256:
            raise RuntimeError('GDB checksum mismatch')
        sock.sendall(b'+')
        return data.decode()

    if packet('Qqemu.PhyMemMode:1') != 'OK':
        raise RuntimeError('physical mode failed')
    try:
        if packet(f'm{pa:x},1') != before.hex():
            raise RuntimeError('live flag differs')
        if packet(f'M{pa:x},1:{a.value:02x}') != 'OK':
            raise RuntimeError('write failed')
        if packet(f'm{pa:x},1') != f'{a.value:02x}':
            raise RuntimeError('readback failed')
    finally:
        if packet('Qqemu.PhyMemMode:0') != 'OK':
            raise RuntimeError('restore virtual mode failed')
if 'paused' not in m.monitor.command('info status'):
    raise RuntimeError('replay unexpectedly resumed')
result = dict(virtual=hex(flag), physical=hex(pa), before=before[0],
              after=a.value, debugger_disconnected=True, paused=True,
              diagnostic_only=True,
              persistent_boot_argument=f'serial={19 if a.value else 3}')
(a.out / 'mutation.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))
