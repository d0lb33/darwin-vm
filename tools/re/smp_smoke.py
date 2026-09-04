#!/usr/bin/env python3
"""Run a two-core T8140 TCG machine smoke test without booting XNU.

Requires the usual firmware inputs and a local clang that accepts -arch arm64.
Writes only /tmp/dvm test artifacts and the owned QEMU's RAM.
"""
import pathlib
import argparse
import socket
import struct
import subprocess
import sys
import time

from smp_trace import Remote

ROOT = pathlib.Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--wfe", action="store_true", help="test Apple event wakeups with all interrupts masked")
    modes.add_argument("--cross-cluster", action="store_true",
                        help="run CPU0/CPU4 with global IPIs in a five-CPU machine")
    options = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    import dt_fixup
    tree = dt_fixup.ADTNode()
    dt_fixup.decode_node((ROOT / "firmware/dtree").read_bytes(), tree)
    assert tree.props["platform-name"] == "t8140"
    chosen = tree["chosen"].props
    base = int(chosen["dram-base"].split(":")[1], 0)
    size = int(chosen["dram-size"].split(":")[1], 0)
    assert size == 8 * 1024**3
    base += size - 0x100000
    qemu = ROOT / "qemu-sptm/build/qemu-system-aarch64"
    inputs = [str(qemu), "-M", "darwin", "-display", "none", "-smp", "2"]
    for option, filename in [("-dtree", "dtree"), ("-bootkc", "bootkc"),
                             ("-tc", "ramdisk.tc"), ("-ramdisk", "ramdisk.dmg")]:
        inputs += [option, str(ROOT / "firmware" / filename)]
    for args, expected in [("cpus=1", "conflicts with -smp 2"),
                           ("cpumask=1", "does not support cpumask")]:
        invalid = subprocess.run(inputs + ["-args", args], capture_output=True,
                                 text=True, timeout=5)
        assert invalid.returncode != 0 and expected in invalid.stderr, invalid.stderr
    print("PASS: conflicting guest CPU boot arguments rejected", flush=True)
    tag = "SMP_MACHINE_WFE" if options.wfe else "SMP_MACHINE_GLOBAL" if options.cross_cluster else "SMP_MACHINE"
    obj = pathlib.Path(f"/tmp/dvm/{tag}.o")
    definitions = ["-DSMP_CROSS_CLUSTER"] if options.cross_cluster else []
    subprocess.run(["clang", "-arch", "arm64", "-c"] + definitions + [
                    str(ROOT / ("tools/re/smp_wfe_smoke.S" if options.wfe else "tools/re/smp_smoke.S")), "-o", str(obj)],
                   check=True)
    data = obj.read_bytes()
    count = struct.unpack_from("<I", data, 16)[0]
    pos = 32
    code = None
    for _ in range(count):
        kind, size = struct.unpack_from("<II", data, pos)
        if kind == 0x19:
            section = pos + 72
            assert data[section:section + 16].split(b"\0")[0] == b"__text"
            length, offset = struct.unpack_from("<QI", data, section + 40)
            assert struct.unpack_from("<I", data, section + 60)[0] == 0
            code = data[offset:offset + length]
            break
        pos += size
    assert code
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    probe = subprocess.Popen([
        str(ROOT / "tools/probe.sh"), "--secs", "15", "--tag", tag,
        "--", "-smp", "5" if options.cross_cluster else "2",
        "-accel", "tcg,thread=multi", "-S", "-gdb",
        f"tcp:127.0.0.1:{port}"], cwd=ROOT)
    remote = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                remote = Remote(port)
                break
            except ConnectionRefusedError:
                time.sleep(0.1)
        assert remote, "owned QEMU gdbstub did not start"
        for pos in range(0, len(code), 1024):
            chunk = code[pos:pos + 1024]
            assert remote.command(f"M{base + pos:x},{len(chunk):x}:{chunk.hex()}") == "OK"
        assert remote.command("P20=" + struct.pack("<Q", base).hex()) == "OK"
        deadline = time.monotonic() + 5
        while True:
            remote.send("c")
            time.sleep(0.05)
            remote.sock.sendall(b"\x03")
            remote.receive()
            reply = remote.command(f"m{base + 0x18000:x},20")
            result = struct.unpack("<8I", bytes.fromhex(reply))
            if result[6] or time.monotonic() >= deadline:
                break
        print("result words:", result[:7], flush=True)
        if options.wfe:
            assert result[:7] == (16, 16, 16, 0, 0, 0, 0x600D), result
            print("PASS: WFE wakes with IRQs masked, both event edges and virtual offset; disabled stream stays asleep", flush=True)
        else:
            assert result[:7] == (1, 1, 1, 40000, 1, 1, 0x600D), result
            print("PASS: secondary reset, shared-memory atomics and bidirectional FIQ IPIs", flush=True)
        remote.command("D")
    finally:
        if remote:
            remote.sock.close()
        probe.wait(timeout=25)


if __name__ == "__main__":
    main()
