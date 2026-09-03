#!/usr/bin/env python3
"""Focused transport tests for tools/serial.py (no QEMU guest required)."""
import importlib.util
import io
import re
import socket
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


if __name__ == "__main__":
    unittest.main()
