"""Read-only snapshots of every vCPU and its frame chain through QEMU GDB."""
import re
import struct
from smp_trace import Remote


def strip_pac(value):
    value &= (1 << 48) - 1
    return value | 0xffff000000000000 if value & (1 << 47) else value


def capture(port, cpus):
    r = Remote(port)  # Attaching pauses this owned QEMU.
    try:
        r.command("?")
        xml = ""
        while True:
            chunk = r.command(f"qXfer:features:read:system-registers.xml:{len(xml):x},1000")
            if not chunk or chunk[0] not in "lm":
                break
            xml += chunk[1:]
            if chunk[0] == "l":
                break
        names = [(name, int(num)) for name, num in re.findall(
            r'<reg name="([^"]+)"[^>]*regnum="([^"]+)"', xml)
            if re.search(r"TPIDR|CNT.*(CTL|CVAL|CT_EL|OFF)|IPI_|ESR_EL|ELR_EL|HCR_EL", name)]

        def memory(address, size):
            reply = r.command(f"m{address:x},{size:x}")
            return None if reply.startswith("E") else bytes.fromhex(reply)

        result = []
        for cpu in range(cpus):
            assert r.command(f"Hg{cpu+1:x}") == "OK"
            raw = bytes.fromhex(r.command("g"))
            regs = struct.unpack_from("<33Q", raw)
            state = {"cpu": cpu, "registers": [hex(x) for x in regs],
                     "pc": hex(regs[32]), "sp": hex(regs[31]),
                     "pstate": hex(struct.unpack_from("<I", raw, 264)[0]),
                     "sysregs": {}, "frames": []}
            for name, num in names:
                reply = r.command(f"p{num:x}")
                if not reply.startswith("E"):
                    state["sysregs"][name] = hex(int.from_bytes(bytes.fromhex(reply), "little"))
            frame = strip_pac(regs[29])
            seen = set()
            for _ in range(16):
                if not frame or frame in seen or frame & 7:
                    break
                seen.add(frame)
                data = memory(frame, 16)
                if data is None:
                    break
                previous, lr = struct.unpack("<QQ", data)
                state["frames"].append({"fp": hex(frame), "lr": hex(strip_pac(lr))})
                frame = strip_pac(previous)
            shared = memory(0xfffffff02b658100, 0x240)
            state["stackshot_context_memory"] = shared.hex() if shared else None
            code = memory(regs[32], 64)
            state["code"] = code.hex() if code else None
            result.append(state)
        return result
    finally:
        # Detach resumes all vCPUs; no debugger stop leaks into the next interval.
        try:
            r.command("D")
        finally:
            r.sock.close()
