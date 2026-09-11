#!/usr/bin/env python3
"""Profile the host cost of a running darwin-vm guest without changing it.

Written 2026-09-06 to answer "what does QEMU burn while iOS sits on the
lock/home screen?".  Everything here is read-only from the guest's point of
view except the HMP ``stop``/``cont`` pair used to read vCPU registers, which
pauses the guest for a few milliseconds per sample.

For a run directory produced by tools/boot_native_smc.py or
tools/warm_boot_probe.py (holding qemu.pid, monitor.sock, serial.log,
stderr.log) it records, over ``--seconds``:

* process %CPU and per-thread CPU-time deltas (``ps -M``), so vCPU threads,
  the main loop and worker threads can be told apart;
* optional macOS ``sample`` call trees of the whole process;
* vCPU PC / EL / instruction samples for every vCPU (``info registers -a``),
  classified as kernel / shared-cache / user / WFI;
* serial.log and stderr.log growth, as a proxy for guest log spam and
  device-model chatter.

Output is one JSON file plus the raw sample text; ``--report`` prints a short
summary.  It never writes to the guest disk and never sends input.
"""
import argparse
import collections
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path


def hmp(path, command, timeout=30):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(str(path))
    buf = b""
    while not buf.rstrip().endswith(b"(qemu)"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.sendall(command.encode() + b"\n")
    buf = b""
    while not buf.rstrip().endswith(b"(qemu)"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    out = []
    for line in buf.decode(errors="replace").replace("\r", "").split("\n"):
        if "\x1b[" in line or line.strip() == "(qemu)":
            continue
        out.append(line)
    return "\n".join(out)


def classify(pc):
    if pc >= 0xfffffff000000000:
        return "kernel"
    if 0x180000000 <= pc < 0x340000000:
        return "user-cache"
    if pc >= 0x100000000:
        return "user-image"
    return "user-low"


def thread_times(pid):
    """Return {thread_index: (cpu_seconds, state)} from ``ps -M``.

    macOS ``ps -M`` lists one row per thread with STIME/UTIME as M:SS.ss.
    Thread ids are not exposed, so rows are keyed by position; QEMU does
    not create or destroy threads once the guest is running, so the
    positions are stable across two samples a few seconds apart.
    """
    out = subprocess.run(["ps", "-M", "-p", str(pid)], capture_output=True, text=True).stdout
    rows = {}
    for i, line in enumerate(out.splitlines()[1:]):
        parts = line.split()
        # First row: USER PID TT %CPU STAT PRI STIME UTIME COMMAND.
        # Continuation rows omit USER and TT: PID %CPU STAT PRI STIME UTIME.
        try:
            if parts[0].isdigit():
                cpu, stat, stime, utime = parts[1], parts[2], parts[4], parts[5]
            else:
                cpu, stat, stime, utime = parts[3], parts[4], parts[6], parts[7]
        except IndexError:
            continue

        def secs(t):
            m, s = t.split(":")
            return int(m) * 60 + float(s)
        rows[i] = (secs(stime) + secs(utime), stat, float(cpu))
    return rows


def vcpu_sample(monitor):
    hmp(monitor, "stop")
    regs = hmp(monitor, "info registers -a")
    hmp(monitor, "cont")
    cpus = []
    cur = None
    for line in regs.splitlines():
        m = re.match(r"\s*CPU#(\d+)", line)
        if m:
            cur = {"cpu": int(m.group(1))}
            cpus.append(cur)
            continue
        if cur is None:
            continue
        m = re.search(r"PC=([0-9a-f]+)", line)
        if m:
            cur["pc"] = int(m.group(1), 16)
        m = re.search(r"PSTATE=([0-9a-f]+) [^ ]+ (EL[0-9][th])", line)
        if m:
            cur["el"] = m.group(2)
            cur["pstate"] = int(m.group(1), 16)
    return cpus


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", type=Path, help="run directory with qemu.pid, monitor.sock, serial.log, stderr.log")
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--vcpu-interval", type=float, default=0.5, help="seconds between vCPU register samples; 0 disables")
    ap.add_argument("--host-sample", type=float, default=0, help="run macOS `sample` for this many seconds (0 disables)")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", a.tag):
        ap.error("bad tag")
    pid = int((a.run / "qemu.pid").read_text().split()[0])
    monitor = a.run / "monitor.sock"
    serial = a.run / "serial.log"
    stderr = a.run / "stderr.log"
    out_dir = Path("/tmp/dvm/idleprof") / a.tag
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    threads0 = thread_times(pid)
    ser0 = serial.stat().st_size if serial.exists() else 0
    err0 = stderr.stat().st_size if stderr.exists() else 0
    ps0 = subprocess.run(["ps", "-o", "cputime=,etime=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()

    sample_proc = None
    if a.host_sample:
        sample_proc = subprocess.Popen(
            ["sample", str(pid), str(int(a.host_sample)), "1", "-mayDie", "-file", str(out_dir / "host-sample.txt")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    vcpu_samples = []
    with (out_dir / "vcpu-samples.txt").open("w") as f:
        while time.time() - t0 < a.seconds:
            if a.vcpu_interval > 0:
                try:
                    cpus = vcpu_sample(monitor)
                except Exception as e:  # keep going; the summary reports gaps
                    f.write("%.3f error %s\n" % (time.time() - t0, e))
                    time.sleep(a.vcpu_interval)
                    continue
                vcpu_samples.append(cpus)
                for c in cpus:
                    pc = c.get("pc", 0)
                    f.write("%.3f cpu%d %s 0x%x %s\n" % (time.time() - t0, c["cpu"], c.get("el", "?"), pc, classify(pc)))
                f.flush()
                time.sleep(a.vcpu_interval)
            else:
                time.sleep(min(1.0, a.seconds))

    elapsed = time.time() - t0
    threads1 = thread_times(pid)
    ser1 = serial.stat().st_size if serial.exists() else 0
    err1 = stderr.stat().st_size if stderr.exists() else 0
    if sample_proc:
        sample_proc.wait()

    per_thread = []
    for i, (t1, stat, cpu) in threads1.items():
        t0v = threads0.get(i, (t1, stat, cpu))[0]
        per_thread.append({"index": i, "cpu_seconds": round(t1 - t0v, 3), "stat": stat, "ps_pct": cpu})
    per_thread.sort(key=lambda r: -r["cpu_seconds"])
    total_cpu = sum(r["cpu_seconds"] for r in per_thread)

    # vCPU summary: fraction of samples per class and per CPU, top PCs.
    per_cpu = collections.defaultdict(collections.Counter)
    top = collections.defaultdict(collections.Counter)
    for cpus in vcpu_samples:
        for c in cpus:
            pc = c.get("pc", 0)
            per_cpu[c["cpu"]][classify(pc)] += 1
            top[c["cpu"]][hex(pc)] += 1

    # Resolve the top PCs' instruction once, so WFI/WFE loops are visible.
    top_insns = {}
    if vcpu_samples:
        try:
            hmp(monitor, "stop")
            for cpu, counter in top.items():
                for pc, _ in counter.most_common(3):
                    hmp(monitor, "cpu %d" % cpu)
                    insn = hmp(monitor, "x/1i %s" % pc).strip().split("\n")[-1].strip()
                    top_insns[pc] = insn
            hmp(monitor, "cpu 0")
        finally:
            hmp(monitor, "cont")

    result = {
        "tag": a.tag, "pid": pid, "elapsed": round(elapsed, 3),
        "ps_before": ps0,
        "process_cpu_seconds": round(total_cpu, 3),
        "process_cpu_pct": round(100 * total_cpu / elapsed, 1),
        "threads": per_thread,
        "serial_bytes_per_s": round((ser1 - ser0) / elapsed, 1),
        "stderr_bytes_per_s": round((err1 - err0) / elapsed, 1),
        "vcpu_samples": len(vcpu_samples),
        "vcpu_classes": {str(k): dict(v) for k, v in per_cpu.items()},
        "vcpu_top_pcs": {str(k): [(pc, n, top_insns.get(pc, "")) for pc, n in v.most_common(5)] for k, v in top.items()},
        "host_sample": str(out_dir / "host-sample.txt") if a.host_sample else None,
    }
    (out_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    if a.report:
        print("process: %.1f%% of one host core over %.1fs (%.2f cpu-s)" % (result["process_cpu_pct"], elapsed, total_cpu))
        print("top threads (cpu-s):", [(r["index"], r["cpu_seconds"]) for r in per_thread[:10]])
        print("serial %.0f B/s, stderr %.0f B/s" % (result["serial_bytes_per_s"], result["stderr_bytes_per_s"]))
        for cpu in sorted(per_cpu):
            print("cpu%d classes=%s" % (cpu, dict(per_cpu[cpu])))
            for pc, n, insn in result["vcpu_top_pcs"][str(cpu)][:3]:
                print("    %s x%d  %s" % (pc, n, insn))
    print("wrote", out_dir / "result.json")


if __name__ == "__main__":
    main()
