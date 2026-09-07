#!/usr/bin/env python3
"""Decode /arm-io/pmgr's `devices`, `ps-groups` and `reg` properties.

Layout from m1n1 src/pmgr.c (struct pmgr_device, 48 bytes):
  u8 flags; u16 unk1; u8 id1; u8/u16 parents; u8 unk3[2]; u8 addr_offset;
  u8 psreg_idx; u8 unk4[4]; u32 group_and_offset (offset:24, group:8);
  u8 unk5[6]; u16 id2; u8 unk6[4]; char name[16].
PMGR_FLAG_VIRTUAL is 0x10.  The T8140 tree carries `ps-groups`, not
`ps-regs`; each ps-group entry is decoded here as (reg index, offset) pairs
of u32, which is the m1n1 "group_and_offset" scheme, and every guess is
printed so it can be checked against a live register trace.

    tools/re/pmgr_devices.py [firmware/dtree.raw] [--grep DISP]
"""
import argparse
import importlib.util
import os
import struct

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_tree(path):
    spec = importlib.util.spec_from_file_location("dt_fixup", os.path.join(REPO, "dt_fixup.py"))
    dtf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dtf)
    tree = dtf.ADTNode()
    dtf.decode_node(open(path, "rb").read(), tree)
    return tree


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dtree", nargs="?", default=os.path.join(REPO, "firmware/dtree.raw"))
    ap.add_argument("--grep", default=None)
    a = ap.parse_args()
    pm = load_tree(a.dtree)["arm-io"]["pmgr"]
    reg = pm.props["reg"]
    regs = [struct.unpack_from("<QQ", reg, i) for i in range(0, len(reg), 16)]
    print("reg windows: %d" % len(regs))
    psg = pm.props.get("ps-groups", b"")
    groups = [struct.unpack_from("<III", psg, i) for i in range(0, len(psg) - 11, 12)]
    print("ps-groups (raw u32 triples):", groups)
    dev = pm.props["devices"]
    n = len(dev) // 48
    print("devices: %d" % n)
    for i in range(n):
        e = dev[i * 48:(i + 1) * 48]
        flags, unk1, id1 = struct.unpack_from("<BHB", e, 0)
        p16 = struct.unpack_from("<HH", e, 4)
        addr_offset, psreg_idx = e[10], e[11]
        gao = struct.unpack_from("<I", e, 16)[0]
        id2 = struct.unpack_from("<H", e, 26)[0]
        name = e[32:48].split(b"\0")[0].decode(errors="replace")
        if a.grep and a.grep.lower() not in name.lower():
            continue
        print("%3d %-16s flags=0x%02x id1=%3d id2=%3d parents=%s addr_off=0x%02x psreg=%d group=%d offset=0x%x%s" % (
            i, name, flags, id1, id2, p16, addr_offset, psreg_idx, gao >> 24, gao & 0xffffff,
            "  VIRTUAL" if flags & 0x10 else ""))


if __name__ == "__main__":
    main()
