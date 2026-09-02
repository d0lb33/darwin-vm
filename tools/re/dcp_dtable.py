#!/usr/bin/env python3
"""Enumerate the DCP link-protocol D-series callback dispatch table.

AppleDCPLinkServiceSoC::link_rpc_lookup (0xfffffff00917d328 in the iOS 27.0b
iPhone17,3 bootkc) maps an incoming FourCC method name to the AP-side handler
the firmware is allowed to call.  It is a chain of compiler-generated switches
-- binary search, jump tables, and pointer arrays -- spread over two kexts, so
reading it by hand is error-prone and does not survive a new kernelcache.

This walks it by *emulating* the dispatcher for every candidate name, which
means the answer is whatever the code actually computes, including the ranges
that never materialise their own name (a jump-table switch builds -base and
does one subtraction, so "D410" appears nowhere in the instruction stream).

    tools/re/dcp_dtable.py [firmware/bootkc]
    tools/re/dcp_dtable.py --entry 0xfffffff00917d328 --namereg 2

For each name found it also reports the handler's argument checks.  The callee
ABI is fixed by rpc_callee_gated at 0xfffffff00a0cf1e8-0xa0cf1fc:

    handler(x0 = chan, x1 = slot, x2 = in, x3 = in_len, x4 = out, x5 = out_len)

and nearly every handler opens with `cmp w3,#N` / `cmp w5,#M` and returns
0xe00002c2 (kIOReturnBadArgument) if the buffer is short, so those two
immediates are the method's declared input and output sizes.

Every address printed is an unslid VA in the same flat space kdis.py uses.
"""
import argparse
import struct
import capstone

BASE = 0xFFFFFFF007004000
PAC_DIV_CALLBACK = 0x6EB1   # rpc_callee_gated's `blraa x23, x17`, x17 = 0x6eb1

# AppleDCPLinkServiceSoC::link_rpc_lookup, the top of the chain.
TOP = 0xFFFFFFF00917D328
TOP_NAMEREG = 2             # `mov x0, x1` then the switch reads w2


class Img:
    def __init__(self, path):
        self.d = open(path, "rb").read()

    def u32(self, va):
        return struct.unpack("<I", self.d[va - BASE:va - BASE + 4])[0]

    def u64(self, va):
        return struct.unpack("<Q", self.d[va - BASE:va - BASE + 8])[0]

    def s32(self, va):
        return struct.unpack("<i", self.d[va - BASE:va - BASE + 4])[0]


M64 = (1 << 64) - 1


def rebase(v):
    """A chained-fixup word -> the VA it names.  Bit 63 marks a fixup; the low
    32 bits are the file offset, which the flat kernelcache mapping turns into
    a VA by adding BASE.  Verified: the __auth_got slot 0xfffffff00808f7c0
    holds 0x80110000030cc538 and 0x30cc538 + BASE == 0xfffffff00a0d0538 ==
    IOMobileGraphicsFamily-DCP's D400 switch, which the switch's own
    `add w16,w1,#-"D400"` independently confirms."""
    if (v >> 32) == 0xFFFFFFF0:
        return v                      # already a kernel VA, not a fixup
    return (BASE + (v & 0xFFFFFFFF)) if (v >> 63) else v


def sx(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


class Emu:
    """Just enough AArch64 to walk a compiler-generated switch."""

    def __init__(self, img):
        self.img = img
        self.md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
        self.md.detail = True
        self.cache = {}

    def insn(self, va):
        i = self.cache.get(va)
        if i is None:
            i = next(self.md.disasm(self.img.d[va - BASE:va - BASE + 4], va))
            self.cache[va] = i
        return i

    # -- register file helpers -------------------------------------------
    def rd(self, name):
        name = name.lower()
        if name in ("xzr", "wzr"):
            return 0
        n = int(name[1:])
        v = self.r[n]
        return v & 0xFFFFFFFF if name[0] == "w" else v

    def wr(self, name, val):
        name = name.lower()
        if name in ("xzr", "wzr"):
            return
        n = int(name[1:])
        self.r[n] = (val & 0xFFFFFFFF) if name[0] == "w" else (val & M64)

    def flags(self, a, b, bits, sub=True):
        """Set NZCV for `a - b` (sub) or `a + b` (add), unsigned width bits."""
        m = (1 << bits) - 1
        a &= m
        b &= m
        if sub:
            res = (a - b) & m
            c = 1 if a >= b else 0
            v = 1 if ((a ^ b) & (a ^ res)) >> (bits - 1) & 1 else 0
        else:
            full = a + b
            res = full & m
            c = 1 if full > m else 0
            v = 1 if (~(a ^ b) & (a ^ res)) >> (bits - 1) & 1 else 0
        self.n = (res >> (bits - 1)) & 1
        self.z = 1 if res == 0 else 0
        self.c = c
        self.v = v
        return res

    def cond(self, cc):
        n, z, c, v = self.n, self.z, self.c, self.v
        return {
            "eq": z, "ne": not z, "hs": c, "cs": c, "lo": not c, "cc": not c,
            "mi": n, "pl": not n, "vs": v, "vc": not v,
            "hi": c and not z, "ls": not c or z,
            "ge": n == v, "lt": n != v,
            "gt": (not z) and n == v, "le": z or n != v,
            "al": True, "nv": True,
        }[cc]

    # -- the interpreter --------------------------------------------------
    def run(self, entry, namereg, name, limit=400):
        self.r = [0] * 32
        self.r[namereg] = name
        self.n = self.z = self.c = self.v = 0
        pc = entry
        for _ in range(limit):
            i = self.insn(pc)
            m, ops = i.mnemonic, [o.strip() for o in i.op_str.split(",")]
            nxt = pc + 4

            if m in ("bti", "nop", "pacibsp", "autibsp", "paciza", "xpacd",
                     "dmb", "hint"):
                pass
            elif m in ("pacia", "pacda", "autda", "autia"):
                pass                       # keep the raw pointer, drop the PAC
            elif m == "mov":
                src = ops[1]
                self.wr(ops[0], int(src[1:], 0) if src.startswith("#")
                        else self.rd(src))
            elif m == "movz":
                self.wr(ops[0], int(ops[1][1:], 0))
            elif m == "movk":
                sh = 0
                if len(ops) > 2 and "lsl" in ops[2]:
                    sh = int(ops[2].split("#")[1], 0)
                cur = self.rd(ops[0])
                imm = int(ops[1][1:], 0)
                self.wr(ops[0], (cur & ~(0xFFFF << sh)) | (imm << sh))
            elif m in ("add", "sub", "adds", "subs", "cmp", "cmn"):
                if m in ("cmp", "cmn"):
                    dst, a_s, b_s = None, ops[0], ops[1]
                    rest = ops[2:]
                else:
                    dst, a_s, b_s = ops[0], ops[1], ops[2]
                    rest = ops[3:]
                bits = 32 if (dst or a_s)[0] in "wW" else 64
                a = self.rd(a_s)
                if b_s.startswith("#"):
                    b = int(b_s[1:], 0)
                else:
                    b = self.rd(b_s)
                for r in rest:
                    if "lsl" in r:
                        b = (b << int(r.split("#")[1], 0)) & M64
                    elif "uxtw" in r:
                        sh = int(r.split("#")[1], 0) if "#" in r else 0
                        b = (b & 0xFFFFFFFF) << sh
                    elif "sxtw" in r:
                        sh = int(r.split("#")[1], 0) if "#" in r else 0
                        b = (sx(b, 32) << sh) & M64
                sub = m in ("sub", "subs", "cmp")
                if m in ("adds", "subs", "cmp", "cmn"):
                    res = self.flags(a, b, bits, sub)
                else:
                    mask = (1 << bits) - 1
                    res = ((a - b) if sub else (a + b)) & mask
                if dst:
                    self.wr(dst, res)
            elif m in ("and", "orr", "eor"):
                a = self.rd(ops[1])
                b = int(ops[2][1:], 0) if ops[2].startswith("#") else self.rd(ops[2])
                self.wr(ops[0], {"and": a & b, "orr": a | b, "eor": a ^ b}[m])
            elif m in ("lsr", "lsl"):
                a = self.rd(ops[1])
                s = int(ops[2][1:], 0) if ops[2].startswith("#") else self.rd(ops[2])
                self.wr(ops[0], (a >> s) if m == "lsr" else (a << s))
            elif m == "csel":
                self.wr(ops[0], self.rd(ops[1]) if self.cond(ops[3]) else self.rd(ops[2]))
            elif m == "csneg":
                v = self.rd(ops[1]) if self.cond(ops[3]) else (-self.rd(ops[2])) & M64
                self.wr(ops[0], v)
            elif m == "cset":
                self.wr(ops[0], 1 if self.cond(ops[1]) else 0)
            elif m == "ccmp":
                if self.cond(ops[3]):
                    b = int(ops[1][1:], 0) if ops[1].startswith("#") else self.rd(ops[1])
                    self.flags(self.rd(ops[0]), b, 32 if ops[0][0] in "wW" else 64)
                else:
                    f = int(ops[2][1:], 0)
                    self.n, self.z = (f >> 3) & 1, (f >> 2) & 1
                    self.c, self.v = (f >> 1) & 1, f & 1
            elif m == "adrp":
                self.wr(ops[0], int(ops[1][1:], 0))
            elif m == "adr":
                self.wr(ops[0], int(ops[1][1:], 0))
            elif m == "ldrsw":
                base, idx = self._mem(ops[1:])
                self.wr(ops[0], self.img.s32(base + idx) & M64)
            elif m == "ldr":
                base, idx = self._mem(ops[1:])
                v = self.img.u64(base + idx) if ops[0][0] in "xX" \
                    else self.img.u32(base + idx)
                self.wr(ops[0], v)
            elif m == "b":
                nxt = int(ops[0][1:], 0)
            elif m.startswith("b.") or m.startswith("bc."):
                if self.cond(m.split(".", 1)[1]):
                    nxt = int(ops[0][1:], 0)
            elif m in ("cbz", "cbnz"):
                t = self.rd(ops[0]) == 0
                if (t if m == "cbz" else not t):
                    nxt = int(ops[1][1:], 0)
            elif m in ("tbz", "tbnz"):
                bit = (self.rd(ops[0]) >> int(ops[1][1:], 0)) & 1
                if (bit == 0) == (m == "tbz"):
                    nxt = int(ops[2][1:], 0)
            elif m == "br":
                nxt = self.rd(ops[0])
            elif m in ("braa", "brab"):
                # A tail call through __auth_got.  The word `ldr x16,[x17]`
                # just loaded is a dyld chained-fixup entry, not a VA: bit 63
                # marks it and the low 32 bits are the target's file offset.
                nxt = rebase(self.rd(ops[0]))
            elif m in ("ret", "retab"):
                return rebase(self.r[0])
            else:
                raise NotImplementedError("%s at 0x%x: %s %s"
                                          % (m, pc, m, i.op_str))
            pc = nxt
        raise RuntimeError("no ret within %d insns from 0x%x" % (limit, entry))

    def _mem(self, ops):
        """Decode `[xN, ...]` operand pieces into (base, index)."""
        txt = ",".join(ops)
        inner = txt[txt.index("[") + 1:txt.rindex("]")]
        parts = [p.strip() for p in inner.split(",")]
        base = self.rd(parts[0])
        idx = 0
        if len(parts) > 1:
            if parts[1].startswith("#"):
                idx = int(parts[1][1:], 0)
            else:
                idx = self.rd(parts[1])
                for r in parts[2:]:
                    if "lsl" in r:
                        idx <<= int(r.split("#")[1], 0)
                    elif "uxtw" in r:
                        idx = (idx & 0xFFFFFFFF) << (int(r.split("#")[1], 0)
                                                     if "#" in r else 0)
                    elif "sxtw" in r:
                        idx = sx(idx, 32) << (int(r.split("#")[1], 0)
                                              if "#" in r else 0)
        return base, idx & M64


def sig(emu, handler):
    """Read a handler's declared in_len/out_len out of its prologue.

    rpc_callee_gated calls handler(chan, slot, in, in_len, out, out_len), and
    the generated stubs open with a length guard that returns
    0xe00002c2 (kIOReturnBadArgument).  Look for `cmp w3,#N` / `cmp w5,#M` in
    the first 24 instructions; anything else is reported as '?'.
    """
    in_len = out_len = None
    va = handler
    for _ in range(48):
        try:
            i = emu.insn(va)
        except StopIteration:
            break
        if i.mnemonic == "cmp":
            ops = [o.strip() for o in i.op_str.split(",")]
            if len(ops) == 2 and ops[1].startswith("#"):
                if ops[0] == "w3" and in_len is None:
                    in_len = int(ops[1][1:], 0)
                elif ops[0] == "w5" and out_len is None:
                    out_len = int(ops[1][1:], 0)
        if i.mnemonic in ("bl", "blraa", "blraaz"):
            break        # past the guard; later cmps are the method's own logic
        va += 4
    return in_len, out_len


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kc", nargs="?", default="firmware/bootkc")
    ap.add_argument("--entry", type=lambda s: int(s, 0), default=TOP)
    ap.add_argument("--namereg", type=int, default=TOP_NAMEREG)
    ap.add_argument("--letter", default="D")
    a = ap.parse_args()

    img = Img(a.kc)
    emu = Emu(img)
    found = []
    for n in range(1000):
        nm = "%s%03d" % (a.letter, n)
        val = struct.unpack(">I", nm.encode())[0]
        try:
            h = emu.run(a.entry, a.namereg, val)
        except Exception as e:                       # noqa: BLE001
            print("%s: emulation stopped: %s" % (nm, e))
            continue
        if h:
            found.append((nm, h))
    for nm, h in found:
        i, o = sig(emu, h)
        print("%s  handler 0x%x  in %s out %s"
              % (nm, h,
                 "0x%x" % i if i is not None else "?",
                 "0x%x" % o if o is not None else "?"))
    print("# %d callbacks accepted by link_rpc_lookup" % len(found))


if __name__ == "__main__":
    main()
