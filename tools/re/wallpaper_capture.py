#!/usr/bin/env python3
"""Wake the native-smc display over the input relay and capture the brightest
lock-screen frame, to check whether a real wallpaper renders.

Usage: wallpaper_capture.py <run_dir> [rounds]
  <run_dir> holds monitor.sock, uart.sock, relay.events (the seed boot dir).

Prints per-frame mean brightness + nonzero-pixel count and saves the brightest
PPM/PNG. An empty (un-seeded) lock screen measures mean ~5/255; a real iOS 17
default wallpaper is far brighter and colorful, so a large jump is the signal.
"""
import os
import socket
import subprocess
import sys
import time

RUN = sys.argv[1].rstrip("/")
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 18
REPO = "/Users/jdolbe1/Downloads/darwin-vm-metal-driver"
MON = RUN + "/monitor.sock"


def hmp(cmd):
    s = socket.socket(socket.AF_UNIX)
    s.connect(MON)
    s.settimeout(6)
    time.sleep(0.05)
    try:
        s.recv(65536)
    except Exception:
        pass
    s.sendall((cmd + "\n").encode())
    time.sleep(0.25)
    try:
        s.recv(65536)
    except Exception:
        pass
    s.close()


def relay(*args):
    cmd = ["python3", REPO + "/tools/input/relay.py",
           "--uart", RUN + "/uart.sock", "--events", RUN + "/relay.events",
           "--log", RUN + "/relay.log"] + list(args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=45).stdout
    except Exception as e:
        return "relay-error: %s" % e


def bright(path):
    d = open(path, "rb").read()
    if d[:2] != b"P6":
        return -1, 0
    i, f = 2, []
    while len(f) < 3:
        while i < len(d) and d[i] in b" \t\n\r":
            i += 1
        j = i
        while j < len(d) and d[j] not in b" \t\n\r":
            j += 1
        f.append(int(d[i:j]))
        i = j
    px = d[i + 1:]
    nz = sum(1 for b in px if b)
    return (sum(px) / len(px) if px else 0), nz


def main():
    open(RUN + "/relay.events", "a").close()
    print("ping:", relay("--ping").strip()[:120])
    print("home:", relay("--home").strip()[:120])
    best = (-1, None)
    for k in range(ROUNDS):
        p = "%s/wp_%02d.ppm" % (RUN, k)
        hmp("screendump " + p)
        if os.path.exists(p):
            b, nz = bright(p)
            print("frame %2d: mean=%.2f nonzero=%d" % (k, b, nz), flush=True)
            if b > best[0]:
                best = (b, p)
        time.sleep(0.5)
    print("BEST", best)
    if best[1]:
        png = best[1].replace(".ppm", ".png")
        try:
            from PIL import Image
            Image.open(best[1]).save(png)
            print("PNG", png)
        except Exception as e:
            print("png-fail", e)


if __name__ == "__main__":
    main()
