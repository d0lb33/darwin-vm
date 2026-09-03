#!/usr/bin/env python3
"""Shared physical-RAM scan for XNU threads and saved kext return addresses.

The older standalone scanners remain useful independently, but a stall
post-mortem needs both result sets. Dumping each RAM chunk once and running
both native regex scans over the same mmap roughly halves monitor transfer and
host-disk work.
"""
import mmap
import os
import re
import struct
import time

import kmem


KSLIDE = 0x20000000
THREAD_SIG_OFFSET = 0x1A0
THREAD_SIG = (0x2010002030100000).to_bytes(8, "little")
THREAD_CHECKS = ((0x1D0, 0xFEEDFACEFEEDFAD3), (0x1D8, 0x2020A52A302ABAE6))
THREAD_SIZE = 0x200


def discover_dram(sock):
    """Return the largest non-tag RAM region from QEMU's flat memory tree."""
    output = kmem.hmp(sock, "info mtree -f")
    best = None
    for line in output.splitlines():
        match = re.search(
            r"([0-9a-fA-F]{8,16})-([0-9a-fA-F]{8,16}) "
            r"\(prio \d+, ram\): (\S+)", line,
        )
        if not match or "tag" in match.group(3).lower():
            continue
        lo, hi = int(match.group(1), 16), int(match.group(2), 16)
        candidate = (lo, hi - lo + 1, match.group(3))
        if best is None or candidate[1] > best[1]:
            best = candidate
    return best


def frame_patterns(ranges, kexts):
    """Build safe prefilters, splitting at each byte-24 address boundary."""
    wanted = [item for item in ranges if any(kext in item[2] for kext in kexts)]
    if not wanted:
        raise ValueError("no kext matched %s" % kexts)
    patterns = []
    for lo, hi, name in wanted:
        slid_lo, slid_hi = lo + KSLIDE, hi + KSLIDE
        cursor = slid_lo
        while cursor < slid_hi:
            boundary = ((cursor >> 24) + 1) << 24
            end = min(slid_hi, boundary)
            byte2_lo = (cursor >> 16) & 0xFF
            byte2_hi = ((end - 1) >> 16) & 0xFF
            if byte2_lo == byte2_hi:
                byte2 = re.escape(bytes([byte2_lo]))
            else:
                byte2 = re.escape(bytes([byte2_lo])) + b"-" + re.escape(bytes([byte2_hi]))
            byte3 = (cursor >> 24) & 0xFF
            pattern = re.compile(
                b"..[" + byte2 + b"]" + re.escape(bytes([byte3])) + b"\xf0",
                re.DOTALL,
            )
            patterns.append((pattern, cursor, end, name))
            cursor = end
    return wanted, patterns


def scan_buffer(mm, dump_base, logical_base, patterns):
    """Return thread rows and frame hits from one mmap'd physical range."""
    logical_offset = logical_base - dump_base
    thread_rows = []
    thread_pattern = re.compile(re.escape(THREAD_SIG))
    for match in thread_pattern.finditer(mm):
        base = match.start() - THREAD_SIG_OFFSET
        if base < 0 or base & 7 or base + THREAD_SIZE > len(mm):
            continue
        if not all(struct.unpack_from("<Q", mm, base + offset)[0] == value
                   for offset, value in THREAD_CHECKS):
            continue
        word = lambda offset: struct.unpack_from("<Q", mm, base + offset)[0]
        thread_rows.append(
            (dump_base + base, word(0x18), word(0xE0), word(0xF0), word(0x10))
        )

    pages = {}
    for pattern, slid_lo, slid_hi, _name in patterns:
        for match in pattern.finditer(mm):
            offset = match.start()
            if offset < logical_offset or offset & 7:
                continue
            word = struct.unpack_from("<Q", mm, offset)[0]
            value = (word & 0xFFFFFFFFFF) | 0xFFFFFFF000000000
            if slid_lo <= value < slid_hi:
                physical = dump_base + offset
                pages.setdefault(physical & ~0x3FFF, []).append((physical & 0x3FFF, value))
    return thread_rows, pages


def scan_range(sock, ram_base, ram_size, chunk_size, workdir, ranges, kexts,
               prefix_overlap=0):
    if ram_size <= 0:
        return [], {}
    if chunk_size <= 0 or chunk_size > 0xFFFFFFFF or chunk_size & 7:
        raise ValueError("chunk must be 8-byte aligned and no larger than 0xffffffff")
    os.makedirs(workdir, exist_ok=True)
    _wanted, patterns = frame_patterns(ranges, kexts)
    kmem.ensure_kernel(sock)
    all_threads = []
    all_pages = {}
    started = time.time()
    for offset in range(0, ram_size, chunk_size):
        logical_base = ram_base + offset
        logical_size = min(chunk_size, ram_size - offset)
        # A thread whose signature begins in this chunk may start up to 0x1a0
        # bytes in the previous one. A small overlap closes that boundary gap.
        overlap = min(THREAD_SIZE, offset + prefix_overlap)
        dump_base = logical_base - overlap
        dump_size = logical_size + overlap
        path = os.path.join(workdir, "chunk-%x-%d.bin" % (logical_base, os.getpid()))
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        kmem.hmp(sock, 'pmemsave 0x%x 0x%x "%s"' % (dump_base, dump_size, path), timeout=600)
        if not os.path.exists(path) or os.path.getsize(path) != dump_size:
            raise RuntimeError("pmemsave failed at 0x%x: expected 0x%x bytes" %
                               (dump_base, dump_size))
        try:
            with open(path, "rb") as stream:
                mm = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
                try:
                    rows, pages = scan_buffer(mm, dump_base, logical_base, patterns)
                finally:
                    mm.close()
            all_threads.extend(rows)
            for page, hits in pages.items():
                all_pages.setdefault(page, []).extend(hits)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        print("scanned 0x%x-0x%x: %d threads, %d frame pages (%.0fs)" %
              (logical_base, logical_base + logical_size, len(all_threads),
               len(all_pages), time.time() - started), flush=True)
    return all_threads, all_pages


def merge_results(first, second):
    rows = list(first[0]) + list(second[0])
    pages = {page: list(hits) for page, hits in first[1].items()}
    for page, hits in second[1].items():
        pages.setdefault(page, []).extend(hits)
    return rows, pages
