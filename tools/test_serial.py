#!/usr/bin/env python3
"""Focused transport tests for tools/serial.py (no QEMU guest required)."""
import importlib.util
import argparse
import base64
import io
import re
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "serial_tool", Path(__file__).with_name("serial.py"))
serial_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(serial_tool)


class SerialHandshakeTests(unittest.TestCase):
    def test_prompt_then_guarded_command_survives_dropped_first_byte(self):
        client, server = socket.socketpair()
        client.setblocking(False)
        received = []

        def guest():
            server.settimeout(1)
            preamble = b""
            while len(preamble) < 2:
                preamble += server.recv(64)
            self.assertEqual(preamble, b"\n\n")
            # A prompt can arrive in fragments, as it does on a real UART.
            server.sendall(b"\r\n#")
            time.sleep(0.01)
            server.sendall(b" ")
            wire = b""
            while not wire.endswith(b"\n"):
                wire += server.recv(256)
            received.append(wire)
            # Model the fault: the first byte after idle disappears.  The
            # guarded byte is the first of a prefix that remains valid after a
            # one-byte loss, leaving the command whole.
            guest_line = wire[1:]
            server.sendall(b"\r\n# " + guest_line + b"\r\n# ")
            server.close()

        thread = threading.Thread(target=guest)
        thread.start()
        log = io.StringIO()
        command = "command -v newfs_apfs"
        self.assertTrue(serial_tool.wait_for_prompt(
            client, re.compile(r"(?:^|[\r\n])# ?"), 1, 0,
            None, log))
        serial_tool.send_command(client, command, 0)
        self.assertTrue(serial_tool.wait_for_text(
            client, re.compile(re.escape(command)), 1, None, log, echo=False))
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(received, [b": :; " + command.encode() + b"\n"])
        client.close()

    def test_missing_prompt_fails_closed(self):
        client, server = socket.socketpair()
        client.setblocking(False)
        log = io.StringIO()
        self.assertFalse(serial_tool.wait_for_prompt(
            client, re.compile(r"(?:^|[\r\n])# ?"), 0.05, 0,
            None, log))
        client.close()
        server.close()


class FakeUploadShell:
    """Small deterministic Unix-socket shell used only by transport tests.

    It models the conditions which motivated the real safeguard: a delayed
    prompt, one leading-byte loss after idle, and one mid-line payload loss.
    It intentionally has no filesystem or QEMU dependency.
    """
    def __init__(self, server, expected):
        self.server = server
        self.expected = expected
        self.parts = {}
        self.dropped_leading = False
        self.corrupted_part = False
        self.decoded = None
        self.error = None

    @staticmethod
    def cksum(data):
        return subprocess.check_output(['cksum'], input=data).decode().split()[0]

    def respond(self, text):
        self.server.sendall(text.encode() + b'\r\n# ')

    def command(self, raw):
        # First real byte after idle disappears.  The guarded form remains a
        # no-op followed by the full command, exactly as the device console
        # should parse it.
        if not self.dropped_leading and raw.startswith(b': :; '):
            raw = raw[1:]
            self.dropped_leading = True
        line = raw.decode('ascii')
        if line.startswith(': :; '):
            line = line[5:]
        elif line.startswith(' :; '):
            line = line[4:]

        part = re.fullmatch(
            r"printf '%s' '([^']*)' > (\S+); /bin/cksum \S+", line)
        if part:
            payload = part.group(1).encode('ascii')
            path = part.group(2)
            # A single lost Base64 character gives a numerically valid but
            # wrong cksum witness.  The client must reject it and overwrite
            # every part in the batch on retry.
            if not self.corrupted_part:
                payload = payload[:7] + payload[8:]
                self.corrupted_part = True
            self.parts[path] = payload
            self.respond('%s %d %s' % (self.cksum(payload), len(payload), path))
            return

        if 'DVM_UPLOAD_JOIN_RC=$?' in line:
            self.joined = b''.join(self.parts[path] for path in sorted(self.parts))
            self.respond('DVM_UPLOAD_JOIN_RC=0')
        elif 'DVM_UPLOAD_DECODE_RC=$?' in line:
            try:
                self.decoded = base64.b64decode(self.joined, validate=True)
                rc = 0
            except (ValueError, AttributeError):
                self.decoded = None
                rc = 1
            self.respond('DVM_UPLOAD_DECODE_RC=%d' % rc)
        elif 'DVM_UPLOAD_SIZE_RC=$?' in line:
            self.respond('DVM_UPLOAD_SIZE_RC=%d' %
                         (0 if self.decoded == self.expected else 1))
        elif 'DVM_UPLOAD_CKSUM_RC=$?' in line:
            self.respond('DVM_UPLOAD_CKSUM_RC=%d' %
                         (0 if self.decoded == self.expected else 1))
        elif 'DVM_UPLOAD_FINAL_RC=$?' in line:
            self.respond('DVM_UPLOAD_FINAL_RC=0 DVM_UPLOAD_BYTES=%d' %
                         len(self.expected))
        else:
            marker = re.search(r'(DVM_UPLOAD_[A-Z_]+_RC)=\$\?', line)
            if not marker:
                raise AssertionError('unhandled fake-shell command: %r' % line)
            self.respond(marker.group(1) + '=0')

    def run(self):
        buf = b''
        try:
            self.server.settimeout(1)
            while True:
                chunk = self.server.recv(256)
                if not chunk:
                    return
                buf += chunk
                while b'\n' in buf:
                    raw, buf = buf.split(b'\n', 1)
                    if raw == b'' or raw == b'\x03':
                        # Delayed prompt makes the client wait rather than
                        # relying on immediate socket connection readiness.
                        time.sleep(0.01)
                        self.respond('')
                    else:
                        self.command(raw)
        except BaseException as exc:  # propagated to the test thread owner
            self.error = exc
        finally:
            self.server.close()


class SerialUploadBatchTests(unittest.TestCase):
    def test_batch_retries_midline_corruption_and_preserves_full_payload(self):
        client, server = socket.socketpair()
        client.setblocking(False)
        payload = b''.join(bytes((n % 251,)) for n in range(511))
        guest = FakeUploadShell(server, payload)
        thread = threading.Thread(target=guest.run)
        thread.start()
        args = argparse.Namespace(prompt_timeout=0.4, echo_timeout=0.15,
                                  secs=0.01, idle=0.01, upload_batch=3)
        log = io.StringIO()
        with tempfile.NamedTemporaryFile() as source:
            source.write(payload)
            source.flush()
            serial_tool.upload(
                client, source.name,
                '/private/var/.dvm-data-seed/fake-upload', 0,
                re.compile(r'(?:^|[\r\n])# ?'), args, None, log)
        client.close()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(guest.error)
        self.assertTrue(guest.dropped_leading)
        self.assertTrue(guest.corrupted_part)
        self.assertEqual(guest.decoded, payload)
        self.assertIn('DVM_UPLOAD_SIZE_RC=0', log.getvalue())
        self.assertIn('DVM_UPLOAD_CKSUM_RC=0', log.getvalue())

    def test_batch_rejects_a_closed_socket_without_all_witnesses(self):
        client, server = socket.socketpair()
        client.setblocking(False)
        server.sendall(b'only-one-marker\r\n')
        server.close()
        self.assertFalse(serial_tool.wait_for_markers(
            client, ['only-one-marker', 'missing-marker'], 0.2,
            None, io.StringIO(), echo=False))
        client.close()


if __name__ == "__main__":
    unittest.main()
