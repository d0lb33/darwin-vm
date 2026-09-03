#!/usr/bin/env python3
"""dt_patch.py - set or delete properties on an already-fixed-up device tree.

dt_fixup.py is orchestrator-owned. This is the experiment tool: it takes a tree
dt_fixup.py already produced and edits single properties on single nodes, so a
bring-up hypothesis ("does RTBuddy start the ANS IOP if the nub says
pre-loaded?") can be tested without changing the shared rewriter. Anything that
proves out here belongs back in dt_fixup.py.

The codec here is deliberately *not* dt_fixup.py's. dt_fixup's decode_prop is
lossy: it turns any all-printable blob into a Python str, and re-encoding
appends a NUL. That is harmless inside dt_fixup, which decodes once and encodes
once from the same values, but fatal for a second round trip -- /chosen's
random-seed is 256 bytes of 'A' after dt_fixup rewrites it (dt_fixup.py:456),
comes back as a 256-character string, and goes out as 257 bytes. SPTM then
refuses to boot:

    [SPTM] ... random-seed (0x...) size mismatch (257) or NULL

So this file keeps every property as raw bytes and only touches the ones named
on the command line. It also preserves the high bit of the length word, which
Apple uses as a "placeholder" flag.

usage:
  tools/dt_patch.py IN OUT \\
      -set  arm-io/ans/iop-ans-nub:pre-loaded=u32:1 \\
      -set  arm-io/ans/iop-ans-nub:no-firmware-service=NULL \\
      -del  arm-io/ans/iop-ans-nub:quiesced

value syntax:
  u32:0x1        4-byte little endian
  u64:0x1000     8-byte little endian
  NULL           zero-length (a boolean property; presence is what is checked)
  hex:0011ff     raw bytes
  anything else  NUL-terminated string, padded to a multiple of 4
"""
import argparse
import os
import struct
import sys


class Node:
    def __init__(self):
        self.props = []          # list of (name, flags, raw_bytes)
        self.children = []

    def name(self):
        for k, _, v in self.props:
            if k == "name":
                return v.split(b"\0")[0].decode("utf8", "replace")
        return None

    def child(self, name):
        for c in self.children:
            if c.name() == name:
                return c
        return None

    def get(self, key):
        for k, _, v in self.props:
            if k == key:
                return v
        return None

    def set(self, key, val):
        for i, (k, f, _) in enumerate(self.props):
            if k == key:
                self.props[i] = (k, f, val)
                return
        self.props.append((key, 0, val))

    def delete(self, key):
        for i, (k, _, _) in enumerate(self.props):
            if k == key:
                del self.props[i]
                return True
        return False


def roundup4(n):
    return (n + 3) & ~3


def decode(buf, off):
    n = Node()
    nprops, nchild = struct.unpack_from("<II", buf, off)
    off += 8
    for _ in range(nprops):
        name = buf[off:off + 32].split(b"\0")[0].decode("utf8", "replace")
        raw_len = struct.unpack_from("<I", buf, off + 32)[0]
        flags = raw_len & 0x80000000
        plen = raw_len & ~0x80000000
        val = buf[off + 36: off + 36 + plen]
        n.props.append((name, flags, val))
        off += 36 + roundup4(plen)
    for _ in range(nchild):
        c, off = decode(buf, off)
        n.children.append(c)
    return n, off


def encode(n):
    out = struct.pack("<II", len(n.props), len(n.children))
    for name, flags, val in n.props:
        assert len(name) < 32, name
        out += name.encode("utf8").ljust(32, b"\0")
        out += struct.pack("<I", len(val) | flags)
        out += val.ljust(roundup4(len(val)), b"\0")
    for c in n.children:
        out += encode(c)
    return out


def parse_value(v):
    if v in ("NULL", "<NULL>", ""):
        return b""
    if v.startswith("u32:"):
        return struct.pack("<I", int(v[4:], 0))
    if v.startswith("u64:"):
        return struct.pack("<Q", int(v[4:], 0))
    if v.startswith("hex:"):
        return bytes.fromhex(v[4:])
    return v.encode("utf8") + b"\0"


def find(root, path):
    n = root
    for part in path.split("/"):
        nxt = n.child(part)
        if nxt is None:
            raise SystemExit("dt_patch: no node %s (stuck at %r)" % (path, part))
        n = nxt
    return n


def main():
    p = argparse.ArgumentParser(prog="dt_patch")
    p.add_argument("infile")
    p.add_argument("outfile")
    p.add_argument("-set", action="append", default=[], dest="sets",
                   metavar="PATH:KEY=VALUE")
    p.add_argument("-del", action="append", default=[], dest="dels",
                   metavar="PATH:KEY")
    a = p.parse_args()

    buf = open(a.infile, "rb").read()
    root, used = decode(buf, 0)

    for s in a.sets:
        loc, _, val = s.partition("=")
        path, _, key = loc.rpartition(":")
        find(root, path).set(key, parse_value(val))
        print("set  %s:%s = %s" % (path, key, val))

    for d in a.dels:
        path, _, key = d.rpartition(":")
        ok = find(root, path).delete(key)
        print("del  %s:%s%s" % (path, key, "" if ok else " (was not present)"))

    out = encode(root)
    open(a.outfile, "wb").write(out)
    print("wrote %s (%d -> %d bytes)" % (a.outfile, len(buf), len(out)))


if __name__ == "__main__":
    main()
