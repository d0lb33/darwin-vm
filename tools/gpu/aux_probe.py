"""Host peer for the disposable raw-media transport experiment.

Only opens the auxiliary file this instance creates. It never opens a guest
filesystem, System/Data disk, or another VM's backend. Shared OS file-cache
visibility is a measured dependency, not an assumed GPU coherency contract.
"""
import json
import os
from pathlib import Path
import struct
import time
import zlib

MAGIC = b'DVM-AUX-TRANSPORT-v1\0'
SIZE = 64 * 1024 * 1024
BULK = 1024 * 1024


class AuxProbe:
    def __init__(self, out):
        self.out = Path(out)
        self.path = self.out/'aux.raw'
        self.header = (MAGIC + os.urandom(32)).ljust(64, b"\0")
        self.seed = bytes((i*37 + (i >> 8)*11 + 19) & 255 for i in range(BULK))
        self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.ftruncate(self.fd, SIZE)
        os.pwrite(self.fd, self.header, 0)
        os.pwrite(self.fd, self.seed, 0x100000)
        self.seen = set()
        self.started = time.monotonic()
        self.log = (self.out/'aux-host.jsonl').open('x')

    def pump(self):
        packet = os.pread(self.fd, 4096, 0x10000)
        if packet[:64] != self.header:
            return
        seq, crc = struct.unpack_from('<II', packet, 64)
        if seq in self.seen or seq not in range(1, 11) or zlib.crc32(packet[72:]) != crc:
            return
        expected = bytes((i*13 + seq*17) & 255 for i in range(72, 4096))
        if packet[72:] != expected:
            raise ValueError('guest request payload differs from expected pattern')
        self.seen.add(seq)
        if seq != 9:
            payload = bytes(b ^ 0xa5 for b in packet[72:])
            response = packet[:68] + struct.pack('<I', zlib.crc32(payload)) + payload
            if os.pwrite(self.fd, response, 0x20000) != len(response):
                raise OSError('short auxiliary response write')
        self.log.write(json.dumps(dict(seconds=time.monotonic()-self.started,
            sequence=seq, request_verified=True, response_written=seq != 9,
            injected_timeout=seq == 9))+'\n')
        self.log.flush()

    def verify(self):
        if os.pread(self.fd, len(self.header), 0) != self.header:
            raise ValueError('dedicated auxiliary header changed')
        actual = os.pread(self.fd, BULK, 0x400000)
        if actual != bytes(b ^ 0x5a for b in self.seed):
            raise ValueError('host did not receive the exact guest bulk output')
        if self.seen != set(range(1, 11)):
            raise ValueError(f'incomplete auxiliary handshake: {sorted(self.seen)}')
        return dict(host_bulk_bytes_verified=BULK, live_requests_verified=10,
            deliberate_timeout_sequence=9, recovered_sequence=10,
            scope='disposable-auxiliary-byte-channel')

    def close(self):
        self.log.close()
        os.close(self.fd)
