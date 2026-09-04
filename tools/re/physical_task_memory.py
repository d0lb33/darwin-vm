"""Read sleeping iOS tasks through QEMU's physical-memory GDB mode.

No guest register or memory writes, scheduling, or RAM scan is required. The
43-bit/16K walk and proc->map (+0x790), map->pmap (+0x58), pmap->root (+8),
proc->thread-list (+0x7b8) layout were cross-checked in DISPLAY_NATIVE_R1:
physical progname matched cfprefsd, com.apple.migrationpluginwrapper, assetsd,
and installcoordinationd; their saved stacks explained both migration waits.

Run inspect_process while stopped at EL1. This helper refuses unsupported page
table geometry and always restores virtual-memory mode before returning.
"""
import json
import lldb
import live_task_threads


def list_processes(debugger, anchor, path):
    """Walk both links from a known live proc; retain truncated kernel names."""
    process = debugger.GetSelectedTarget().GetProcess()
    records = {}
    for link in (0, 8):
        address, seen = anchor, set()
        for _ in range(512):
            if not address or address in seen or address & 7:
                break
            seen.add(address)
            try:
                raw = live_task_threads._read(process, address, 0x57c)
            except RuntimeError:
                break
            name = raw[0x55c:0x56c].split(b"\0", 1)[0].decode("ascii", "replace")
            if not name or not name.isascii() or not name.isprintable():
                break
            records[address] = {"proc": address, "name": name}
            address = int.from_bytes(raw[link:link + 8], "little")
    result = list(records.values())
    with open(path, "w") as stream:
        json.dump(result, stream, indent=2)
    print("LIVE_PROCESS_COUNT %d path=%s" % (len(result), path))
    return result


def packet(debugger, command):
    result = lldb.SBCommandReturnObject()
    debugger.GetCommandInterpreter().HandleCommand(
        "process plugin packet send " + command, result)
    if not result.Succeeded():
        raise RuntimeError(result.GetError())
    output = result.GetOutput()
    if "response:" not in output:
        raise RuntimeError("missing GDB response: " + output)
    return output.split("response:", 1)[1].strip()


class PhysicalTaskMemory:
    def __init__(self, debugger, root):
        self.debugger = debugger
        self.root = root
        frame = debugger.GetSelectedTarget().GetProcess().GetSelectedThread().GetFrameAtIndex(0)
        tcr = frame.FindRegister("tcr_el1").GetValueAsUnsigned()
        if tcr & 0x3f != 21 or (tcr >> 14) & 3 != 2:
            raise RuntimeError("requires verified 43-bit VA / 16K granule")

    def __enter__(self):
        if packet(self.debugger, "qqemu.PhyMemMode") != "0":
            raise RuntimeError("GDB is already in physical-memory mode")
        if packet(self.debugger, "Qqemu.PhyMemMode:1") != "OK":
            raise RuntimeError("cannot enable physical-memory mode")
        return self

    def __exit__(self, *_):
        if packet(self.debugger, "Qqemu.PhyMemMode:0") != "OK":
            raise RuntimeError("cannot restore virtual-memory mode; do not resume")

    def physical(self, address, size):
        # Raw packets avoid LLDB's virtual-address memory cache entirely.
        raw = packet(self.debugger, "m%x,%x" % (address, size))
        try:
            data = bytes.fromhex(raw)
        except ValueError as error:
            raise RuntimeError("physical read failed at 0x%x: %s" %
                               (address, raw)) from error
        if len(data) != size:
            raise RuntimeError("short physical read at 0x%x" % address)
        return data

    def read(self, address, size):
        if not 0 <= address < 1 << 43 or not 0 <= size <= 0x100000:
            raise ValueError("user address/size out of bounds")
        output = bytearray()
        while len(output) < size:
            va = address + len(output)
            table = self.root & 0x0000fffffffffc00
            for level, shift in ((1, 36), (2, 25), (3, 14)):
                index = (va >> shift) & (0x7f if level == 1 else 0x7ff)
                entry = int.from_bytes(self.physical(table + index * 8, 8), "little")
                if not entry & 1 or (level == 3 and entry & 3 != 3):
                    raise RuntimeError("unmapped VA 0x%x level %d" % (va, level))
                if entry & 3 == 1 or level == 3:
                    mask = (1 << shift) - 1
                    pa = (entry & 0x0000ffffffffffff & ~mask) | (va & mask)
                    break
                table = entry & 0x0000ffffffffc000
            length = min(size - len(output), 0x4000 - (va & 0x3fff))
            output.extend(self.physical(pa, length))
        return bytes(output)

    def u64(self, address):
        return int.from_bytes(self.read(address, 8), "little")

    def progname(self, slide):
        pointer = self.u64(0x1e6ef1590 + slide)
        string = self.u64(pointer)
        return self.read(string, 96).split(b"\0", 1)[0].decode("ascii", "replace")

    def chains(self, rows):
        results = []
        for row in rows:
            fp, seen, frames = row.get("fp", 0), set(), []
            while fp and fp not in seen and not fp & 7 and len(frames) < 64:
                seen.add(fp)
                try:
                    raw = self.read(fp, 16)
                except (RuntimeError, ValueError):
                    break
                frames.append({"fp": fp, "lr": int.from_bytes(raw[8:], "little") & 0xffffffffffff})
                fp = int.from_bytes(raw[:8], "little")
            results.append({"thread": row["thread"], "pc": row.get("pc"), "frames": frames})
        return results


def inspect_process(debugger, proc, path, slide=0x4b30000):
    process = debugger.GetSelectedTarget().GetProcess()
    def read(address, size):
        return live_task_threads._read(process, address, size)
    def u64(address):
        return int.from_bytes(read(address, 8), "little")
    kernel_name = read(proc + 0x55c, 16).split(b"\0", 1)[0].decode("ascii", "replace")
    vm_map = u64(proc + 0x790)
    pmap = u64(vm_map + 0x58)
    root = u64(pmap + 8)
    rows = live_task_threads.walk(debugger, u64(proc + 0x7b8), path + ".threads.json")
    with PhysicalTaskMemory(debugger, root) as memory:
        name = memory.progname(slide)
        if name[:len(kernel_name)] != kernel_name:
            raise RuntimeError("process mapping mismatch: %r != %r" % (kernel_name, name))
        chains = memory.chains(rows)
    result = {"proc": proc, "name": name, "map": vm_map, "pmap": pmap,
              "root": root, "threads": rows, "chains": chains}
    with open(path, "w") as stream:
        json.dump(result, stream, indent=2)
    print("PHYSICAL_TASK name=%s proc=0x%x root=0x%x" % (name, proc, root))
    for chain in chains:
        print("PHYSICAL_TASK_CHAIN thread=0x%x frames=%s" %
              (chain["thread"], ",".join("0x%x" % f["lr"] for f in chain["frames"])))
    return result
