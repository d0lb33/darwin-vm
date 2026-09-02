#!/usr/bin/env python3
"""Merge two or more raw (IM4P-already-unwrapped) TrustCacheModule2_t blobs
into a single sorted module, so multiple trust caches can be fed through the
single-file -tc option in xnuboot_sptm.c without any C changes.

Real iBoot hands XNU a *list* of modules -- qemu-sptm/include/xnu/boot/
trustcache.h, trust_cache_offsets_t { num_caches; offsets[] } -- but
get_tcinfo() synthesizes num_caches=1 for whatever single file -tc names, so
we merge on the host instead.

Format (xnu-13432-era, confirmed empirically against 24A5430a):
  header: version(u32 LE)=2, uuid(16 bytes), numEntries(u32 LE)
  entry (24 bytes): hash(20) + hashType(1) + flags(1) + constraintCategory(u16 LE)

v1 modules (the restore ramdisk still ships one) have 22-byte entries and no
constraint category; they are converted on load by zero-extending, which is
what "no constraint category" means to AMFI.

Entries must be sorted ascending by hash -- Apple's own modules are, and
build_tc.py sorts too. Derivation in docs/re/rootfs-assembly.md, "Problem 3".
"""
import struct, sys

HDR = struct.Struct("<I16sI")

def load(path):
    data = open(path, "rb").read()
    version, uuid, n = HDR.unpack_from(data, 0)
    if version == 2:
        elen = 24
    elif version == 1:
        elen = 22
    else:
        raise SystemExit(f"{path}: unsupported trust cache version {version}")
    if len(data) != HDR.size + n * elen:
        raise SystemExit(f"{path}: size mismatch (v{version}, {n} entries, {len(data)} B)")
    off = HDR.size
    entries = []
    for i in range(n):
        e = data[off + i*elen : off + (i+1)*elen]
        if elen == 22:
            e = e + b"\x00\x00"          # zero constraint category
        entries.append(e)
    return version, uuid, entries

def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: merge_tc.py out.bin in1.bin [in2.bin ...]")
    out_path, in_paths = sys.argv[1], sys.argv[2:]
    all_entries, seen, dupes, uuid0 = [], set(), 0, None
    for p in in_paths:
        version, uuid, entries = load(p)
        if uuid0 is None:
            uuid0 = uuid
        for e in entries:
            h = e[:20]
            if h in seen:
                dupes += 1
                continue
            seen.add(h)
            all_entries.append(e)
        print(f"  {p}: v{version}, {len(entries)} entries")
    all_entries.sort(key=lambda e: e[:20])
    with open(out_path, "wb") as f:
        f.write(HDR.pack(2, uuid0, len(all_entries)))
        for e in all_entries:
            f.write(e)
    print(f"merged {len(in_paths)} modules -> {len(all_entries)} unique entries "
          f"({dupes} duplicate hashes dropped), uuid={uuid0.hex()}, out={out_path}")

if __name__ == "__main__":
    main()
