#!/usr/bin/env python3
"""Turn probe log events into an early-stop request.

This is deliberately separate from LLDB.  Breakpoint callbacks must return so
the inferior can continue; sleeping in a callback to decide that a call is
stuck would itself stop the guest.  Instead callbacks emit one-line
``PROBE_EVENT`` records and this process follows the log while QEMU runs.

Examples::

    probe_watch.py --stop-file /tmp/dvm/T.stop \
      --event-dir /tmp/dvm/T.events --selector 0x4f --pending-secs 30

    probe_watch.py --stop-file /tmp/dvm/T.stop \
      --stop-on /tmp/dvm/probe/T.serial.log 'set_power_state done powerState=0'

The watcher never talks to QEMU.  It atomically writes the stop file; probe.sh
owns the freeze and verdict so there is only one shutdown path.
"""
import argparse
import json
import os
import re
import sys
import time


class Follower:
    """Read complete lines appended to a file, surviving create/truncate."""

    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.carry = ""

    def read_lines(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self.offset:
            self.offset = 0
            self.carry = ""
        try:
            with open(self.path, "rb") as stream:
                stream.seek(self.offset)
                data = stream.read()
                self.offset = stream.tell()
        except OSError:
            return []
        text = self.carry + data.decode("utf-8", "replace")
        parts = text.split("\n")
        self.carry = parts.pop()
        return [line.rstrip("\r") for line in parts]


def write_stop(path, reason):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = "%s.%d.tmp" % (path, os.getpid())
    with open(temporary, "w") as stream:
        stream.write(reason.replace("\n", " ") + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop-file", required=True)
    parser.add_argument(
        "--stop-on", action="append", nargs=2, default=[], metavar=("FILE", "REGEX"),
        help="request a stop when a newly read line in FILE matches REGEX",
    )
    parser.add_argument(
        "--event-dir",
        help="directory containing atomic ready/pending/success events from LLDB callbacks",
    )
    parser.add_argument("--selector", type=lambda value: int(value, 0))
    parser.add_argument("--pending-secs", type=float, default=30.0)
    parser.add_argument("--poll-secs", type=float, default=0.25)
    parser.add_argument(
        "--max-secs", type=float, default=0.0,
        help="exit without requesting a stop after this long; zero waits forever",
    )
    args = parser.parse_args(argv)
    if (args.event_dir is None) != (args.selector is None):
        parser.error("--event-dir and --selector must be supplied together")
    if not args.stop_on and args.event_dir is None:
        parser.error("at least one --stop-on or an event selector is required")
    if args.pending_secs < 0 or args.poll_secs <= 0 or args.max_secs < 0:
        parser.error("durations must be non-negative and poll-secs must be positive")

    watches = []
    for path, pattern in args.stop_on:
        try:
            watches.append((Follower(path), re.compile(pattern), path))
        except re.error as error:
            parser.error("invalid regex %r: %s" % (pattern, error))
    started = time.monotonic()

    print("probe_watch: waiting for a stop condition", flush=True)
    while True:
        for follower, pattern, path in watches:
            for line in follower.read_lines():
                if pattern.search(line):
                    reason = "matched-log path=%s regex=%s line=%s" % (path, pattern.pattern, line)
                    write_stop(args.stop_file, reason)
                    print("probe_watch: " + reason, flush=True)
                    return 0

        if args.event_dir is not None and os.path.exists(os.path.join(args.event_dir, "ready")):
            now = time.time()
            overdue = []
            try:
                names = os.listdir(args.event_dir)
            except OSError:
                names = []
            for name in names:
                if not name.startswith("pending.") or not name.endswith(".json"):
                    continue
                path = os.path.join(args.event_dir, name)
                try:
                    with open(path) as stream:
                        event = json.load(stream)
                    if int(event["selector"]) != args.selector:
                        continue
                    entered = float(event["time"])
                    if now - entered >= args.pending_secs:
                        overdue.append((int(event["call_id"]), entered, path))
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    continue
            if overdue:
                call_id, entered, pending_path = min(overdue, key=lambda item: item[1])
                claimed_path = os.path.join(args.event_dir, "claimed.%d.json" % call_id)
                returned_path = os.path.join(args.event_dir, "returned.%d.json" % call_id)
                try:
                    # Atomic claim: if the return callback removed pending first,
                    # this fails and no stop is requested.
                    os.replace(pending_path, claimed_path)
                except FileNotFoundError:
                    continue
                # Give an already-running return callback one polling quantum to
                # publish its durable return record and revoke the claim.
                time.sleep(min(args.poll_secs, 0.25))
                if os.path.exists(returned_path) or not os.path.exists(claimed_path):
                    try:
                        os.unlink(claimed_path)
                    except FileNotFoundError:
                        pass
                    continue
                reason = ("selector-deadline selector=0x%x call_id=%d age=%.1fs" %
                          (args.selector, call_id, now - entered))
                write_stop(args.stop_file, reason)
                print("probe_watch: " + reason, flush=True)
                return 0

        if args.max_secs and time.monotonic() - started >= args.max_secs:
            print("probe_watch: max wait expired without a stop condition", flush=True)
            return 3
        time.sleep(args.poll_secs)


if __name__ == "__main__":
    sys.exit(main())
