"""Read the measured iOS 27 install-cleanup batch without scheduling tasks.

IXSCoordinatedAppInstall ivars come from installcoordinationd ObjC metadata;
seed.identity is +0x28, MIAppIdentity.bundleID is +0x10 (static 0x1aae23884).
Dispatch group +0x30 decreases by four per enter (static 0x1ae8bb368..374).
Call while stopped at EL1, passing the verified task root and item-array VA.
"""
import json
from physical_task_memory import PhysicalTaskMemory


def string(memory, address, swift_storage_class=None):
    raw = memory.read(address, 32)
    flags = int.from_bytes(raw[8:10], "little")
    # Exact constant/inline UTF-8 forms seen for iBooks and WeatherPosterApp.
    if (swift_storage_class is not None and
            int.from_bytes(raw[:8], "little") == swift_storage_class and
            int.from_bytes(raw[24:32], "little") >> 48 == 0xf000):
        # R15: __StringStorage (cache class 0x1e6f0c220) holds these plugin
        # names as tail-allocated UTF-8 at +0x20; count is +0x18 low 48 bits.
        pointer = address + 32
        length = int.from_bytes(raw[24:32], "little") & 0xffffffffffff
        capacity = int.from_bytes(raw[16:24], "little")
        if length > capacity:
            return "<invalid Swift string count/capacity>"
    elif flags == 0x7c8:
        pointer = int.from_bytes(raw[16:24], "little")
        length = int.from_bytes(raw[24:32], "little")
    elif flags == 0x78c:
        pointer, length = address + 17, raw[16]
    else:
        return "<unsupported NSString flags=0x%x at 0x%x>" % (flags, address)
    if length > 256:
        return "<implausible bundle ID length>"
    return memory.read(pointer, length).split(b"\0", 1)[0].decode("utf-8", "replace")


def inspect(debugger, root, items, count, coordinator_class, path):
    if not 0 < count <= 128:
        raise ValueError("require a measured bounded batch count")
    rows = []
    with PhysicalTaskMemory(debugger, root) as memory:
        for index in range(count):
            address = memory.u64(items + index * 8)
            row = {"index": index, "coordinator": address}
            try:
                raw = memory.read(address, 0x168)
                isa = int.from_bytes(raw[:8], "little") & 0x7fffffffff8
                if isa != coordinator_class:
                    raise ValueError("unexpected coordinator class 0x%x" % isa)
                seed = int.from_bytes(raw[0xe0:0xe8], "little")
                identity = memory.u64(seed + 0x28)
                row["bundle_id"] = string(memory, memory.u64(identity + 0x10))
                group = int.from_bytes(raw[0xf8:0x100], "little")
                state = memory.u64(group + 0x30) & 0xffffffff
                row.update(group=group, group_state=state,
                           outstanding=((-(state & 0xfffffffc)) & 0xffffffff) // 4,
                           complete=bool(raw[0xa]), tracked=bool(raw[0xb]),
                           expect_assertion_callback=bool(raw[0xf]),
                           placeholder_state=int.from_bytes(raw[0x158:0x160], "little"),
                           app_state=int.from_bytes(raw[0x160:0x168], "little"))
            except (RuntimeError, ValueError) as error:
                row["error"] = str(error)
            rows.append(row)
    with open(path, "w") as stream:
        json.dump(rows, stream, indent=2)
    for row in rows:
        print("INSTALL_COORDINATOR " + json.dumps(row, sort_keys=True))
    return rows
