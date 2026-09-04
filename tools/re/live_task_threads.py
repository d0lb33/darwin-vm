"""Read a bounded XNU thread queue and saved userspace state with guest LLDB.

The iOS 27 b8 saved-state pointer at thread+0x110 is established by bootkc
0xfffffff00aa62cb0..0xfffffff00aa62cc8. Validate every thread signature and
saved-state flavor before interpreting its fields. Run while stopped at EL1.
"""
import json
import lldb


def _read(process, address, size):
    error = lldb.SBError()
    raw = process.ReadMemory(address, size, error)
    if error.Fail() or len(raw) != size:
        raise RuntimeError("unreadable 0x%x+0x%x" % (address, size))
    return raw


def _ptr(value):
    return value | 0xffff000000000000 if value else 0


def walk(debugger, anchor, path, link=0x408):
    process = debugger.GetSelectedTarget().GetProcess()
    rows, seen = [], set()
    address = anchor
    for _ in range(256):
        if address in seen:
            break
        seen.add(address)
        raw = _read(process, address, 0x440)
        word = lambda offset: int.from_bytes(raw[offset:offset+8], "little")
        if word(0x1a0) != 0x2010002030100000:
            print("THREAD_QUEUE_SENTINEL address=0x%x next=0x%x" %
                  (address, word(0)))
            address = _ptr(word(0))
            if not address:
                break
            continue
        saved = _ptr(word(0x110))
        state = _read(process, saved, 0x110) if saved else b""
        row = {"thread": address, "wait_event": word(0x18),
               "continuation": word(0xe0), "kernel_stack": word(0xf0),
               "state": word(0x1f0), "group": word(0x290), "saved": saved}
        if state and int.from_bytes(state[:4], "little") == 0x15:
            for label, offset in (("fp",0xf0),("lr",0xf8),
                                  ("sp",0x100),("pc",0x108)):
                row[label] = int.from_bytes(state[offset:offset+8], "little")
        rows.append(row)
        address = _ptr(word(link))
        if not address:
            break
    with open(path, "w") as stream:
        json.dump({"anchor": anchor,"link": link,"rows": rows,
                   "next":address,"closed":address in seen},stream,indent=2)
    for row in rows:
        print("LIVE_THREAD " + " ".join("%s=0x%x" % item for item in row.items()))
    print("LIVE_THREAD_COUNT %d" % len(rows), flush=True)
    return rows


def user_chains(debugger, rows, path):
    """Read saved frame chains while the selected task's mapping is active."""
    process = debugger.GetSelectedTarget().GetProcess()
    result = []
    for row in rows:
        fp, seen, frames = row.get("fp", 0), set(), []
        for _ in range(64):
            if not fp or fp in seen or fp & 7:
                break
            seen.add(fp)
            try:
                raw = _read(process, fp, 16)
            except RuntimeError:
                break
            parent = int.from_bytes(raw[:8], "little")
            lr = int.from_bytes(raw[8:], "little") & 0x0000ffffffffffff
            frames.append({"fp": fp, "lr": lr})
            fp = parent
        result.append({"thread": row["thread"], "pc": row.get("pc"),
                       "frames": frames})
        print("USER_CHAIN thread=0x%x pc=0x%x frames=%s" %
              (row["thread"], row.get("pc",0),
               ",".join("0x%x" % f["lr"] for f in frames)))
    with open(path,"w") as stream:
        json.dump(result,stream,indent=2)
    return result


def kernel_chains(debugger, rows, path):
    """Inspect only each known thread's 16K kernel stack, without a RAM scan.

    Keep candidate frame chains distinct from an unwound current context: the
    stack may contain older frames. Require ascending in-stack frame pointers
    and a canonical kernel return address at every link.
    """
    process = debugger.GetSelectedTarget().GetProcess()
    result = []
    for row in rows:
        base = row.get("kernel_stack", 0)
        if not base:
            continue
        try:
            raw = _read(process, base, 0x4000)
        except RuntimeError:
            continue
        links = {}
        for offset in range(0, len(raw) - 15, 16):
            parent = _ptr(int.from_bytes(raw[offset:offset+8], "little"))
            lr = _ptr(int.from_bytes(raw[offset+8:offset+16], "little"))
            if base + offset < parent < base + len(raw) and not parent & 15 and 0xfffffff007004000 <= lr < 0xfffffff040000000:
                links[base + offset] = (parent, lr)
        chains = []
        for start in links.keys() - {v[0] for v in links.values()}:
            fp, frames = start, []
            while fp in links:
                parent, lr = links[fp]
                frames.append({"fp": fp, "lr": lr})
                fp = parent
            if len(frames) >= 2:
                chains.append(frames)
        chains.sort(key=len, reverse=True)
        result.append({"thread": row["thread"], "kernel_stack": base,
                       "candidate_chains": chains})
        print("KERNEL_STACK_CANDIDATES thread=0x%x count=%d longest=%s" %
              (row["thread"], len(chains),
               [hex(f["lr"]) for f in chains[0]] if chains else []))
    with open(path, "w") as stream:
        json.dump(result, stream, indent=2)
    return result
