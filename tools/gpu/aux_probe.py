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
    def __init__(self, out, latency=False):
        self.out = Path(out)
        self.path = self.out/'aux.raw'
        self.header = (MAGIC + os.urandom(32)).ljust(64, b"\0")
        self.seed = bytes((i*37 + (i >> 8)*11 + 19) & 255 for i in range(BULK))
        self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.ftruncate(self.fd, SIZE)
        os.pwrite(self.fd, self.header, 0)
        self.latency = latency
        self.request_count = 64 if latency else 10
        self.config = b'DVMLAT01' + struct.pack('<II', 64, 1000000) if latency else bytes(16)
        os.pwrite(self.fd, self.config, 128)
        os.pwrite(self.fd, self.seed, 0x100000)
        self.seen = set()
        self.started = time.monotonic()
        self.last_poll_ns = None
        self.max_poll_gap_ns = 0
        self.polls = 0
        self.first_request_ns = None
        self.first_cpu_ns = None
        self.last_response_ns = None
        self.last_cpu_ns = None
        self.records = []
        self.log = (self.out/'aux-host.jsonl').open('x')

    def pump(self):
        poll_ns = time.monotonic_ns()
        poll_gap_ns = poll_ns-self.last_poll_ns if self.last_poll_ns is not None else None
        if self.last_poll_ns is not None:
            self.max_poll_gap_ns = max(self.max_poll_gap_ns, poll_ns-self.last_poll_ns)
        self.last_poll_ns = poll_ns
        self.polls += 1
        packet = os.pread(self.fd, 4096, 0x10000)
        observed_ns = time.monotonic_ns()
        if packet[:64] != self.header:
            return
        seq, crc = struct.unpack_from('<II', packet, 64)
        if seq in self.seen or seq not in range(1, self.request_count+1) or zlib.crc32(packet[72:]) != crc:
            return
        expected = bytes((i*13 + seq*17) & 255 for i in range(72, 4096))
        if packet[72:] != expected:
            raise ValueError('guest request payload differs from expected pattern')
        validated_ns = time.monotonic_ns()
        if self.latency and seq != len(self.seen)+1:
            raise ValueError('out-of-order latency request')
        self.seen.add(seq)
        if self.first_request_ns is None:
            self.first_request_ns = observed_ns
            self.first_cpu_ns = time.process_time_ns()
        timeout = not self.latency and seq == 9
        if not timeout:
            payload = bytes(b ^ 0xa5 for b in packet[72:])
            response = packet[:68] + struct.pack('<I', zlib.crc32(payload)) + payload
            if os.pwrite(self.fd, response, 0x20000) != len(response):
                raise OSError('short auxiliary response write')
        published_ns = time.monotonic_ns()
        self.last_response_ns = published_ns
        self.last_cpu_ns = time.process_time_ns()
        record = dict(seconds=time.monotonic()-self.started,
            sequence=seq, request_verified=True, response_written=not timeout,
            injected_timeout=timeout, poll_ns=poll_ns, observed_ns=observed_ns,
            validated_ns=validated_ns, handled_ns=published_ns,
            host_service_ns=published_ns-observed_ns,
            host_reply_ns=published_ns-validated_ns, poll_gap_ns=poll_gap_ns,
            polls_since_request=self.polls, max_poll_gap_ns=self.max_poll_gap_ns)
        if self.latency:
            self.records.append(record)
        else:
            self.log.write(json.dumps(record)+'\n')
            self.log.flush()
        self.max_poll_gap_ns = 0
        self.polls = 0

    def verify(self):
        if os.pread(self.fd, len(self.header), 0) != self.header:
            raise ValueError('dedicated auxiliary header changed')
        if os.pread(self.fd, 16, 128) != self.config:
            raise ValueError('auxiliary experiment configuration changed')
        actual = os.pread(self.fd, BULK, 0x400000)
        if actual != bytes(b ^ 0x5a for b in self.seed):
            raise ValueError('host did not receive the exact guest bulk output')
        if self.seen != set(range(1, self.request_count+1)):
            raise ValueError(f'incomplete auxiliary handshake: {sorted(self.seen)}')
        if self.latency:
            return dict(host_bulk_bytes_verified=BULK, live_requests_verified=64,
                host_active_wall_ns=self.last_response_ns-self.first_request_ns,
                host_runner_active_cpu_ns=self.last_cpu_ns-self.first_cpu_ns,
                scope='disposable-auxiliary-latency-channel')
        return dict(host_bulk_bytes_verified=BULK, live_requests_verified=10,
            deliberate_timeout_sequence=9, recovered_sequence=10,
            scope='disposable-auxiliary-byte-channel')

    def close(self):
        for record in self.records:
            self.log.write(json.dumps(record)+'\n')
        self.log.close()
        os.close(self.fd)
