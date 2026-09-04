#!/usr/bin/env python3
"""Bounded AppleARMSMP startup trace for iPhone17,3 24A5430a.

Launch probe.sh with -S -gdb tcp:127.0.0.1:PORT, then run this script.
Addresses are firmware-specific, unslid + 0x20000000. No guest patches.
"""
import argparse
import socket
import struct
import time


class Remote:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=3)
        self.sock.settimeout(20)

    def send(self, text):
        data = text.encode()
        self.sock.sendall(b"$" + data + b"#%02x" % (sum(data) & 255))

    def receive(self):
        while True:
            c = self.sock.recv(1)
            if not c:
                raise EOFError("gdbstub closed")
            if c == b"$":
                break
        data = b""
        while True:
            c = self.sock.recv(1)
            if not c:
                raise EOFError("gdbstub closed")
            if c == b"#":
                break
            data += c
        checksum = b""
        while len(checksum) < 2:
            c = self.sock.recv(2 - len(checksum))
            if not c:
                raise EOFError("gdbstub closed during checksum")
            checksum += c
        assert int(checksum, 16) == (sum(data) & 255)
        self.sock.sendall(b"+")
        # gdbstub can use run-length encoding even without negotiation.
        out = bytearray()
        i = 0
        while i < len(data):
            if data[i] == ord("*"):
                i += 1
                out.extend([out[-1]] * (data[i] - 29))
            else:
                out.append(data[i])
            i += 1
        return out.decode()

    def command(self, command):
        self.send(command)
        return self.receive()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", type=int)
    parser.add_argument("--pc", type=lambda v: int(v, 0), action="append")
    args = parser.parse_args()
    remote = Remote(args.port)
    points = {
        0xFFFFFFF02B2F8068: "IOPlatformExpert wait returned",
        0xFFFFFFF02B2F8174: "PassthruInterruptController ready",
        0xFFFFFFF02B2F8198: "IOPMGR wait returned",
        0xFFFFFFF02B2F8204: "CPU registration begins",
    }
    if args.pc:
        points = {pc: hex(pc) for pc in args.pc}
    for address in points:
        assert remote.command(f"Z1,{address:x},4") == "OK"
    end = time.monotonic() + 25
    try:
        while time.monotonic() < end:
            remote.send("c")
            remote.receive()
            data = bytes.fromhex(remote.command("g"))
            regs = struct.unpack_from("<33Q", data)
            pc = regs[32]
            print(points.get(pc, hex(pc)),
                  " ".join(f"x{i}={regs[i]:#x}" for i in [0, 1, 19, 20, 21, 30]),
                  flush=True)
            if pc not in points:
                raise RuntimeError(f"unexpected stop at {pc:#x}")
            assert remote.command(f"z1,{pc:x},4") == "OK"
            remote.command("s")
            assert remote.command(f"Z1,{pc:x},4") == "OK"
    except socket.timeout:
        print("No further startup breakpoint within timeout", flush=True)
    finally:
        remote.sock.close()


if __name__ == "__main__":
    main()
