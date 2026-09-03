#!/usr/bin/env python3
"""One-shot post-mortem of a frozen guest's display-related kernel stalls.

The first pass defaults to 2 GiB. If it finds no correlated kext stack, the
untouched DRAM suffix is scanned automatically; the prefix is never re-read.
Use ``--first-pass-only`` for deliberately partial triage. Outputs remain at
``/tmp/dvm/TAG.{threads,ramscan,postmortem}.txt`` and temporary files are
isolated under ``/tmp/dvm/ramscan/TAG``.
"""
import argparse
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kmem  # noqa: E402
from kc_text_map import load_map, attribute  # noqa: E402
from ramscan_stall import discover_dram, merge_results, scan_range  # noqa: E402


TAG_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def attr(ranges, value):
    value = (value & 0xFFFFFFFFFF) | 0xFFFFFFF000000000
    found = attribute(ranges, value - kmem.KSLIDE)
    return "%s+0x%x" % (found[0].split(".")[-1], found[1]) if found and found[0] else "?"


def write_raw(thread_path, frame_path, rows, pages, ranges, completeness):
    with open(thread_path, "w") as stream:
        stream.write("# scan=%s\n" % completeness)
        stream.write("# thread_pa wait_event continuation kernel_stack +0x10\n")
        for row in sorted(set(rows)):
            stream.write("0x%x 0x%x 0x%x 0x%x 0x%x\n" % row)
    with open(frame_path, "w") as stream:
        stream.write("# scan=%s\n" % completeness)
        for page in sorted(pages):
            hits = sorted(set(pages[page]))
            stream.write("page 0x%x (%d hits)\n" % (page, len(hits)))
            for offset, value in hits:
                found = attribute(ranges, value - kmem.KSLIDE)
                name = found[0].split(".")[-1] if found and found[0] else "?"
                delta = found[1] if found and found[0] else 0
                stream.write("  +0x%04x  0x%x  %s+0x%x\n" %
                             (offset, value, name, delta))


def emit_postmortem(sock, output, rows, pages, ranges, kexts, max_hits, workdir):
    by_stack = {}
    for row in rows:
        stack = kmem.kptr(row[3]) & ~0x3FFF
        if stack:
            by_stack[stack] = row
    emitted = 0
    with open(output, "w") as stream:
        for physical in sorted(pages):
            hit_count = len(set(pages[physical]))
            if hit_count > max_hits:
                stream.write("# dense page 0x%x skipped (%d hits > %d); use --max-hits to inspect\n" %
                             (physical, hit_count, max_hits))
                continue
            page_file = os.path.join(workdir, "page-%x.bin" % physical)
            try:
                os.unlink(page_file)
            except FileNotFoundError:
                pass
            kmem.hmp(sock, 'pmemsave 0x%x 0x4000 "%s"' % (physical, page_file), timeout=60)
            if not os.path.exists(page_file) or os.path.getsize(page_file) != 0x4000:
                raise RuntimeError("short page dump at 0x%x" % physical)
            try:
                with open(page_file, "rb") as page_stream:
                    data = page_stream.read()
            finally:
                os.unlink(page_file)
            words = [struct.unpack_from("<Q", data, offset)[0]
                     for offset in range(0, 0x4000, 8)]
            pointer_bases = [kmem.kptr(word) & ~0x3FFF for word in words]
            candidates = [stack for stack in by_stack if stack in pointer_bases]
            if not candidates:
                continue
            stack_va = max(candidates, key=pointer_bases.count)
            thread = by_stack[stack_va]
            frames = []
            for index in range(len(words) - 1):
                fp = kmem.kptr(words[index])
                lr = (words[index + 1] & 0xFFFFFFFFFF) | 0xFFFFFFF000000000
                if ((fp & ~0x3FFF) == stack_va and kmem.KTEXT_LO <= lr < kmem.KTEXT_HI
                        and (fp & 0x3FFF) > 8 * index):
                    frames.append("  +0x%04x fp->+0x%04x lr=%s" %
                                  (8 * index, fp & 0x3FFF, attr(ranges, lr)))
            if not any(any(kext.split(".")[-1] in frame for kext in kexts)
                       for frame in frames):
                continue
            header = ("page 0x%x stack VA 0x%x  thread_pa=0x%x wait_event=0x%x "
                      "continuation=%s" %
                      (physical, stack_va, thread[0], thread[1],
                       attr(ranges, thread[2]) if thread[2] else "0"))
            print(header)
            stream.write(header + "\n")
            for frame in frames:
                print(frame)
                stream.write(frame + "\n")
            emitted += 1
    return emitted


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sock")
    parser.add_argument("tag")
    parser.add_argument("--kext", action="append", default=None)
    parser.add_argument("--max-hits", type=int, default=80)
    parser.add_argument("--min-stacks", type=int, default=1,
                        help="fall back to full DRAM unless this many correlated stacks are found")
    parser.add_argument("--ram-base", type=lambda value: int(value, 0))
    parser.add_argument("--ram-size", type=lambda value: int(value, 0), default=0x80000000,
                        help="fast first-pass size (default 2 GiB)")
    parser.add_argument("--full-ram-size", type=lambda value: int(value, 0),
                        help="complete DRAM size; discovered from QEMU when omitted")
    parser.add_argument("--chunk", type=lambda value: int(value, 0), default=0x40000000)
    parser.add_argument("--first-pass-only", action="store_true",
                        help="never scan the suffix; output is explicitly marked partial")
    parser.add_argument("--bootkc", default="firmware/bootkc")
    args = parser.parse_args(argv)
    if not TAG_RE.fullmatch(args.tag):
        parser.error("TAG must contain only letters, digits, dot, underscore, or dash")
    if args.ram_size <= 0 or args.max_hits <= 0 or args.min_stacks <= 0:
        parser.error("ram-size, max-hits, and min-stacks must be positive")

    kexts = args.kext or ["AppleFirmwareKit", "driver.RTBuddy", "AppleDCP",
                          "IOMobileGraphicsFamily"]
    ranges = load_map(args.bootkc)
    discovered = discover_dram(args.sock)
    if args.ram_base is None:
        if not discovered:
            parser.error("could not discover DRAM; pass --ram-base and --full-ram-size")
        ram_base = discovered[0]
    else:
        ram_base = args.ram_base
    if args.full_ram_size is None:
        if not discovered:
            parser.error("could not discover DRAM; pass --full-ram-size")
        full_size = discovered[1]
    else:
        full_size = args.full_ram_size
    if full_size <= 0:
        parser.error("full-ram-size must be positive")

    first_size = min(args.ram_size, full_size)
    workdir = os.path.join("/tmp/dvm/ramscan", args.tag)
    os.makedirs(workdir, exist_ok=True)
    thread_path = "/tmp/dvm/%s.threads.txt" % args.tag
    frame_path = "/tmp/dvm/%s.ramscan.txt" % args.tag
    output = "/tmp/dvm/%s.postmortem.txt" % args.tag

    result = scan_range(args.sock, ram_base, first_size, args.chunk, workdir, ranges, kexts)
    completeness = "complete" if first_size == full_size else "partial-first-pass"
    write_raw(thread_path, frame_path, result[0], result[1], ranges, completeness)
    emitted = emit_postmortem(args.sock, output, result[0], result[1], ranges,
                              kexts, args.max_hits, workdir)

    if emitted < args.min_stacks and not args.first_pass_only and first_size < full_size:
        print("found %d/%d required correlated stacks in first 0x%x bytes; scanning untouched suffix" %
              (emitted, args.min_stacks, first_size))
        suffix = scan_range(args.sock, ram_base + first_size, full_size - first_size,
                            args.chunk, workdir, ranges, kexts,
                            prefix_overlap=min(0x200, first_size))
        result = merge_results(result, suffix)
        completeness = "complete-after-fallback"
        write_raw(thread_path, frame_path, result[0], result[1], ranges, completeness)
        emitted = emit_postmortem(args.sock, output, result[0], result[1], ranges,
                                  kexts, args.max_hits, workdir)

    if args.first_pass_only and first_size < full_size:
        print("PARTIAL scan: inspected 0x%x of 0x%x DRAM bytes" % (first_size, full_size))
    elif emitted:
        print("wrote %s (%d correlated stacks; %s)" % (output, emitted, completeness))
    else:
        print("wrote %s (no correlated stacks; %s)" % (output, completeness))
    return 0


if __name__ == "__main__":
    sys.exit(main())
