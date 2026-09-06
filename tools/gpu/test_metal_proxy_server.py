#!/usr/bin/env python3
"""Host-only framed-protocol test for metal_proxy_server."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import signal
import subprocess
import time

EXPECTED_AIR_SHA256 = "8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364"
WIDTH, HEIGHT, BYTES = 64, 48, 64 * 48 * 4
CHILDREN = []


def expire(_signum, _frame):
    raise TimeoutError("whole host-only test exceeded its outer timeout")


def read_line(stream):
    line = stream.readline()
    if not line.endswith(b"\n"):
        raise AssertionError(f"short protocol line: {line!r}")
    return line


def write_command(process, command, payload=b""):
    assert process.stdin
    process.stdin.write(command)
    process.stdin.write(payload)
    process.stdin.flush()


def start(server):
    child = subprocess.Popen([str(server)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    CHILDREN.append(child)
    return child


def failed_process(process, expected):
    assert process.stdin and process.stdout and process.stderr
    line = read_line(process.stdout)
    if not re.fullmatch(expected, line):
        raise AssertionError(f"unexpected protocol error: {line!r}")
    process.stdin.close()
    stderr = process.stderr.read().decode("utf-8", "replace")
    rc = process.wait(timeout=10)
    if rc == 0:
        raise AssertionError("malformed command was accepted")
    return {"stdout": line.decode().strip(), "stderr": stderr, "exit": rc}


def pattern(generation, run):
    out = bytearray(BYTES)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            i = (y * WIDTH + x) * 4
            out[i] = (x * 17 + y * 31 + generation * 43 + run * 59) & 0xff
            out[i + 1] = (x * 7 + y * 13 + generation * 19 + run * 29) & 0xff
            out[i + 2] = (x ^ (y * 3) ^ (generation * 97) ^ (run * 71)) & 0xff
            out[i + 3] = (255 - x - y - generation * 11 - run * 5) & 0xff
    return bytes(out)


def test_good_path(server, library):
    process = start(server)
    assert process.stdin and process.stdout and process.stderr
    write_command(process, f"LIB 1 {len(library)}\n".encode(), library)
    assert read_line(process.stdout) == b"OK 1\n"
    write_command(process, b"PIPE 2 read_write_surf_compute\n")
    assert read_line(process.stdout) == b"OK 2\n"
    evidence = []
    command_id = 3
    for generation in range(3):
        for run in range(3):
            image = pattern(generation, run)
            # This verifies that the response waits at least 200ms after GPU completion.
            delay = 200 if (generation, run) == (0, 0) else (0, 7, 19)[run]
            began = time.monotonic()
            write_command(process,
                          f"RUN {command_id} {generation} 64 48 {delay} 12288\n".encode(), image)
            assert read_line(process.stdout) == f"DATA {command_id} 12288\n".encode()
            result = process.stdout.read(BYTES)
            elapsed_ms = (time.monotonic() - began) * 1000
            if result != image:
                raise AssertionError(f"GPU output differs for generation={generation} run={run}")
            if delay == 200 and elapsed_ms < 180:
                raise AssertionError(f"200ms response delay was only {elapsed_ms:.3f}ms")
            evidence.append({"id": command_id, "generation": generation, "run": run,
                             "delay_ms": delay, "elapsed_ms": round(elapsed_ms, 3),
                             "sha256": hashlib.sha256(result).hexdigest()})
            command_id += 1
    write_command(process, b"END\n")
    process.stdin.close()
    stderr = process.stderr.read().decode("utf-8", "replace")
    rc = process.wait(timeout=10)
    if rc != 0:
        raise AssertionError(f"good server exited {rc}: {stderr}")
    if b"" != process.stdout.read():
        raise AssertionError("unexpected stdout after END")
    if stderr.count("textures=recreated") != 3 or stderr.count("textures=reused") != 6:
        raise AssertionError(f"generation reuse diagnostics missing: {stderr}")
    if stderr.count("pre_dispatch_differs=1") != 9:
        raise AssertionError(f"negative-control diagnostics missing: {stderr}")
    return {"runs": evidence, "stderr": stderr}


def test_negative_length(server):
    process = start(server)
    write_command(process, b"LIB 1 -1\n")
    return failed_process(process, rb"ERR 1 PROTO [0-9]+\n")


def test_excess_length(server):
    process = start(server)
    write_command(process, b"LIB 1 12582913\n")
    return failed_process(process, rb"ERR 1 PROTO [0-9]+\n")


def test_stale_id(server, library):
    process = start(server)
    assert process.stdout
    write_command(process, f"LIB 1 {len(library)}\n".encode(), library)
    assert read_line(process.stdout) == b"OK 1\n"
    write_command(process, b"PIPE 1 read_write_surf_compute\n")
    return failed_process(process, rb"ERR 1 PROTO [0-9]+\n")


def test_wrong_dimensions(server, library):
    process = start(server)
    assert process.stdout
    write_command(process, f"LIB 1 {len(library)}\n".encode(), library)
    assert read_line(process.stdout) == b"OK 1\n"
    write_command(process, b"PIPE 2 read_write_surf_compute\n")
    assert read_line(process.stdout) == b"OK 2\n"
    # The invalid header is rejected before a payload is read.
    write_command(process, b"RUN 3 0 63 48 0 12288\n")
    return failed_process(process, rb"ERR 3 PROTO [0-9]+\n")


def test_real_invalid_library(server):
    process = start(server)
    write_command(process, b"LIB 1 1\n", b"\0")
    result = failed_process(process, rb"ERR 1 [^\s]+ -?[0-9]+\n")
    fields = result["stdout"].split()
    domain, code = fields[2], fields[3]
    if domain in {"METAL", "PROTO", "IO"}:
        raise AssertionError(f"library error lost NSError domain: {result['stdout']}")
    result["nserror_domain"] = domain
    result["nserror_code"] = code
    return result


def parse_args():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", type=Path, default=root / "metal_proxy_server")
    parser.add_argument("--library", type=Path,
                        default=Path("/tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib"))
    parser.add_argument("--output", type=Path, default=root / "test-evidence.json")
    parser.add_argument("--timeout", type=int, default=40)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.timeout < 1:
        raise SystemExit("--timeout must be positive")
    if not args.server.is_file() or not args.library.is_file():
        raise SystemExit("--server and --library must be files")
    library = args.library.read_bytes()
    actual_sha = hashlib.sha256(library).hexdigest()
    if actual_sha != EXPECTED_AIR_SHA256:
        raise SystemExit(f"unexpected AIR SHA-256: {actual_sha}")
    signal.signal(signal.SIGALRM, expire)
    signal.alarm(args.timeout)
    try:
        output = {
            "library": str(args.library), "library_sha256": actual_sha,
            "good": test_good_path(args.server, library),
            "negative_length": test_negative_length(args.server),
            "excess_length": test_excess_length(args.server),
            "stale_id": test_stale_id(args.server, library),
            "wrong_dimensions": test_wrong_dimensions(args.server, library),
            "invalid_library": test_real_invalid_library(args.server),
        }
    finally:
        signal.alarm(0)
        for child in CHILDREN:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"passed": True, "runs": len(output["good"]["runs"]),
                      "invalid_library_domain": output["invalid_library"]["nserror_domain"]}))


if __name__ == "__main__":
    main()
