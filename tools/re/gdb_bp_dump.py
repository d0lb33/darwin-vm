#!/usr/bin/env python3
"""Stop at runtime PCs over QEMU's gdbstub and dump registers plus memory.

A generic version of smp_trace.py for bring-up loops: launch probe.sh with
`NO_WATCHDOG=1 ... -- -S -gdb tcp:127.0.0.1:PORT`, then

    tools/re/gdb_bp_dump.py PORT --pc 0xfffffff0292622ac \
        --mem x1:0x60 --mem 'x1+0x50:*:0x100' --hits 2 --timeout 120

`--mem REG[+OFF][:*][:LEN]` dumps LEN bytes at REG+OFF; a `*` dereferences
the 8-byte pointer found there first (`x1+0x50:*:0x100` dumps the object
pointed to by the word at x1+0x50). Every stop prints all 31 GPRs, sp, pc.
Addresses are runtime (kernel slide +0x20000000 on this firmware).
"""
import argparse
import re
import socket
import struct
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from smp_trace import Remote  # noqa: E402


def hexdump(base, data):
    for i in range(0, len(data), 32):
        chunk = data[i:i + 32]
        words = " ".join("%016x" % struct.unpack_from("<Q", chunk, j)[0]
                         for j in range(0, len(chunk) - 7, 8))
        print("  %016x: %s" % (base + i, words))


def read_mem(remote, addr, length):
    out = bytearray()
    while len(out) < length:
        n = min(0x200, length - len(out))
        reply = remote.command("m%x,%x" % (addr + len(out), n))
        if reply.startswith("E") or not reply:
            raise RuntimeError("memory read failed at 0x%x: %r" % (addr + len(out), reply))
        out += bytes.fromhex(reply)
    return bytes(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", type=int)
    ap.add_argument("--pc", type=lambda v: int(v, 0), action="append", required=True)
    ap.add_argument("--mem", action="append", default=[])
    ap.add_argument("--hits", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=120)
    a = ap.parse_args()
    remote = Remote(a.port)
    remote.sock.settimeout(a.timeout)
    for pc in a.pc:
        assert remote.command("Z1,%x,4" % pc) == "OK", "breakpoint refused"
    hits = 0
    try:
        while hits < a.hits:
            remote.send("c")
            remote.receive()
            regs = struct.unpack_from("<33Q", bytes.fromhex(remote.command("g")))
            pc = regs[32]
            print("=== stop at pc=0x%x (hit %d)" % (pc, hits + 1))
            for i in range(0, 31, 4):
                print("  " + " ".join("x%-2d=%016x" % (j, regs[j]) for j in range(i, min(i + 4, 31))))
            print("  sp=%016x pc=%016x" % (regs[31], pc))
            for spec in a.mem:
                # REG followed by any sequence of +OFF (add) and * (deref),
                # then an optional :LEN, e.g. x1+0x50:*+0x98:*:0x40
                m = re.fullmatch(r"(x\d+|sp)((?::?\*|\+(?:0x[0-9a-f]+|\d+))*)(?::(0x[0-9a-f]+|\d+))?", spec)
                if not m:
                    print("  bad --mem", spec)
                    continue
                reg = 31 if m.group(1) == "sp" else int(m.group(1)[1:])
                addr = regs[reg]
                length = int(m.group(3), 0) if m.group(3) else 0x40
                label = spec
                try:
                    for op in re.findall(r":?\*|\+(?:0x[0-9a-f]+|\d+)", m.group(2)):
                        if op.endswith("*"):
                            addr = struct.unpack("<Q", read_mem(remote, addr, 8))[0]
                            label += " -> 0x%x" % addr
                        else:
                            addr += int(op[1:], 0)
                    print("  [%s]" % label)
                    hexdump(addr, read_mem(remote, addr, length))
                except RuntimeError as e:
                    print("  [%s]" % label, e)
            hits += 1
            if pc in a.pc:
                assert remote.command("z1,%x,4" % pc) == "OK"
                remote.command("s")
                assert remote.command("Z1,%x,4" % pc) == "OK"
            else:
                print("  (stop was not at a requested breakpoint)")
                break
    except socket.timeout:
        print("timeout waiting for a breakpoint")
    finally:
        remote.sock.close()


if __name__ == "__main__":
    main()
