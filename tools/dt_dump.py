#!/usr/bin/env python3
"""dt_dump.py - print one device tree node's properties, reg windows decoded.

The ADT parser lives in dt_fixup.py; this just reuses it so bring-up work can
read ground truth out of firmware/dtree without hand-rolling a decoder each
time (and without editing dt_fixup.py, which is orchestrator-owned).

usage:  tools/dt_dump.py firmware/dtree arm-io/ans [arm-io/sart-ans ...]
"""
import importlib.util
import os
import struct
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("dt_fixup", os.path.join(REPO, "dt_fixup.py"))
dtf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dtf)


def child(node, name):
    for c in node.children:
        if c.props.get("name") == name:
            return c
    return None


def fmt(k, v):
    if isinstance(v, (bytes, bytearray)):
        return "%s (%d bytes)" % (v.hex(), len(v))
    return str(v)


def show(root, path, depth=1):
    n = root
    for part in path.split("/"):
        n = child(n, part)
        if n is None:
            print("%s: NOT FOUND" % path)
            return
    print("=== /%s ===" % path)
    for k, v in n.props.items():
        if k == "reg" and isinstance(v, (bytes, bytearray)) and len(v) % 16 == 0:
            for i in range(len(v) // 16):
                b, l = struct.unpack_from("<QQ", v, i * 16)
                print("  reg[%2d] base 0x%x len 0x%x" % (i, b, l))
        else:
            print("  %-28s = %s" % (k, fmt(k, v)))
    if depth:
        for c in n.children:
            show_node(c, "  ", depth - 1)


def show_node(n, indent, depth):
    print("%schild %s:" % (indent, n.props.get("name")))
    for k, v in n.props.items():
        print("%s  %-26s = %s" % (indent, k, fmt(k, v)))
    if depth:
        for c in n.children:
            show_node(c, indent + "  ", depth - 1)


def main():
    raw = open(sys.argv[1], "rb").read()
    root = dtf.ADTNode()
    dtf.decode_node(raw, root)
    for path in sys.argv[2:]:
        show(root, path)


if __name__ == "__main__":
    main()
