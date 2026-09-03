#!/usr/bin/env python3
"""Bounded live IOMFB call tracing through QEMU's gdb-remote stub.

This is deliberately a *trace*, not a debugger front end: every breakpoint
callback records a small, fixed set of registers, makes only small read-only
guest-memory reads, and immediately continues the guest.  The addresses below
are the iOS 27 iPhone17,3 runtime addresses with the measured +0x20000000
kernel slide already applied.  Do not apply a second slide.

Use it from LLDB's embedded Python (macOS's system Python does not ship the
``_lldb`` extension):

  /usr/bin/lldb -b \
    -o 'command script import /absolute/path/tools/re/iomfb_calltrace.py' \
    -o 'iomfb-calltrace --host 127.0.0.1 --port 12345 --duration 120 \
        --output /tmp/dvm/iomfb-calltrace.jsonl'

Offline commands intentionally do not import lldb, so these are usable with
the normal system Python too:

  python3 tools/re/iomfb_calltrace.py --dry-run
  python3 tools/re/iomfb_calltrace.py --address-map
  python3 tools/re/iomfb_calltrace.py --validate-address-map my-map.json

Evidence for the sites: A401's wrapper is documented in
docs/re/iomfb-link.md:649-677; D575's handler and its hot-plug-shaped vtable
call are documented in docs/re/boot-idle.md:145-153; the default-framebuffer
and A482 control flow was disassembled at /tmp/dvm/defaultfb.dis during the
display-runtime trace plan.  ``surface_map_dcp`` is the DART/swap boundary
identified in docs/re/ca-software-path.md:331-340.
"""

import argparse
import json
import os
import shlex
import sys
import time
from collections import defaultdict


# iOS 27 iPhone17,3, all runtime/slid addresses (+0x20000000 already applied).
# Keep this map literal and validate it before a live trace: changing one
# address silently turns a negative result into a false conclusion.
RUNTIME_ADDRESSES = {
    "a401": 0xfffffff02a0c8a80,
    "hotplug_entry": 0xfffffff02a0bcad8,
    "d575_handler": 0xfffffff02a0d9e4c,
    "d575_callsite": 0xfffffff02a0d9f7c,
    "default_fb_public": 0xfffffff02a0c02c0,
    "default_fb_gated": 0xfffffff02a0c03e8,
    "default_local_create_return": 0xfffffff02a0c04ec,
    "a482_pre": 0xfffffff02a0c05f8,
    "a482_post": 0xfffffff02a0c05fc,
    "a482_wrapper": 0xfffffff02a0cc25c,
    "surface_map_entry": 0xfffffff02a0b9600,
    "surface_map_return": 0xfffffff02a0b97a0,
}

SITE_ORDER = tuple(RUNTIME_ADDRESSES)
DEFAULT_SITE_CAP = 8
MAX_MEMORY_READ = 0x80
MAX_STATE_RECORDS = 64
MAX_CALLBACK_ERRORS = 8

# `SBBreakpoint` only offers the script-callback API on Apple's LLDB; it does
# not expose the C++/Python `SetCallback` convenience method.  The registered
# global resolves a live breakpoint ID back to this trace session and site.
# A run owns the map while it is connected and clears it on detach.
_BREAKPOINT_OWNERS = {}


def _parse_int(value):
    """Argparse integer parser that accepts decimal and C-style hex."""
    return int(value, 0)


def _hex(value, width=16):
    """Render an unsigned target register/address without JSON number loss."""
    return "0x%0*x" % (width, value & ((1 << (width * 4)) - 1))


def validate_address_map(addresses):
    """Return a normalized complete runtime address map or raise ValueError.

    The tracer intentionally refuses partial maps and non-word-aligned values.
    A bad map is more dangerous than no map because it makes an absent event
    look meaningful.
    """
    if not isinstance(addresses, dict):
        raise ValueError("address map must be a JSON object")
    expected = set(SITE_ORDER)
    got = set(addresses)
    missing = sorted(expected - got)
    extra = sorted(got - expected)
    if missing or extra:
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(missing))
        if extra:
            detail.append("unknown: " + ", ".join(extra))
        raise ValueError("invalid address-map keys (" + "; ".join(detail) + ")")

    normalized = {}
    for site in SITE_ORDER:
        value = addresses[site]
        if isinstance(value, str):
            try:
                value = int(value, 0)
            except ValueError as exc:
                raise ValueError("%s is not an integer address" % site) from exc
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("%s is not an integer address" % site)
        if value < 0xffff000000000000 or value > 0xffffffffffffffff:
            raise ValueError("%s is not a canonical kernel address: %r" %
                             (site, value))
        if value & 3:
            raise ValueError("%s is not instruction aligned: %s" %
                             (site, _hex(value)))
        normalized[site] = value
    return normalized


def load_address_map(path):
    """Load a complete map from a small JSON file, then validate it."""
    with open(path, "r", encoding="utf-8") as handle:
        return validate_address_map(json.load(handle))


def parse_site_caps(values, default_cap):
    """Parse repeatable SITE=N caps, rejecting typos rather than ignoring them."""
    if default_cap < 1:
        raise ValueError("--site-cap must be at least one")
    caps = {site: default_cap for site in SITE_ORDER}
    for value in values:
        site, sep, count = value.partition("=")
        if not sep or site not in caps:
            raise ValueError("--cap must be SITE=N for one of: %s" %
                             ", ".join(SITE_ORDER))
        try:
            count_i = int(count, 0)
        except ValueError as exc:
            raise ValueError("invalid cap for %s: %s" % (site, count)) from exc
        if count_i < 1:
            raise ValueError("cap for %s must be at least one" % site)
        caps[site] = count_i
    return caps


def make_parser(prog=None):
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Trace the iOS IOMFB display/runtime gates through gdb-remote.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="QEMU gdb-remote host")
    parser.add_argument("--port", type=int, default=12345,
                        help="QEMU gdb-remote TCP port")
    parser.add_argument("--duration", type=float, default=120.0,
                        help="seconds to trace; zero traces until interrupted")
    parser.add_argument("--output", "-o", default="-",
                        help="JSONL destination, or - for stdout")
    parser.add_argument("--site-cap", type=int, default=DEFAULT_SITE_CAP,
                        help="maximum JSONL records per breakpoint site")
    parser.add_argument("--cap", action="append", default=[], metavar="SITE=N",
                        help="override one site cap (repeatable)")
    parser.add_argument("--map", metavar="FILE",
                        help="complete replacement JSON runtime address map")
    parser.add_argument("--address-map", action="store_true",
                        help="print the built-in, already-slid address map and exit")
    parser.add_argument("--validate-address-map", metavar="FILE",
                        help="validate a complete JSON map and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate inputs and print the proposed trace without LLDB")
    return parser


def _json_map(addresses):
    return {site: _hex(addresses[site]) for site in SITE_ORDER}


def offline_main(argv=None):
    """Run only CLI/map validation; safe outside LLDB and without a guest."""
    args = make_parser().parse_args(argv)
    if args.port < 1 or args.port > 65535:
        raise ValueError("--port must be in 1..65535")
    if args.duration < 0:
        raise ValueError("--duration cannot be negative")

    builtin = validate_address_map(RUNTIME_ADDRESSES)
    if args.address_map:
        print(json.dumps(_json_map(builtin), indent=2, sort_keys=True))
        return 0
    if args.validate_address_map:
        checked = load_address_map(args.validate_address_map)
        print(json.dumps({"valid": True, "address_map": _json_map(checked)},
                         sort_keys=True))
        return 0

    addresses = load_address_map(args.map) if args.map else builtin
    caps = parse_site_caps(args.cap, args.site_cap)
    if args.dry_run:
        print(json.dumps({
            "address_map": _json_map(addresses),
            "duration_seconds": args.duration,
            "endpoint": "%s:%d" % (args.host, args.port),
            "output": args.output,
            "site_caps": caps,
        }, indent=2, sort_keys=True))
        return 0
    return args, addresses, caps


class CallTracer:
    """LLDB callback state.  Every callback returns False to auto-continue."""

    def __init__(self, lldb, process, stream, addresses, caps):
        self.lldb = lldb
        self.process = process
        self.stream = stream
        self.addresses = addresses
        self.caps = caps
        self.hits = defaultdict(int)
        self.emitted = defaultdict(int)
        self.callback_errors = defaultdict(int)
        self.breakpoint_ids = []

    @staticmethod
    def _register(frame, name):
        """Read xN/wN/lr robustly across LLDB's arm64 register contexts."""
        try:
            reg = frame.FindRegister(name)
            if reg and reg.IsValid():
                return reg.GetValueAsUnsigned()
        except Exception:
            pass
        # Some LLDB builds expose the register set only through __getitem__.
        try:
            reg = frame.register[name]
            return reg.GetValueAsUnsigned()
        except Exception:
            return None

    def _memory(self, address, size):
        """Small, read-only memory capture.  Failure becomes data, not a stop."""
        if address is None or address < 0x1000:
            return {"address": _hex(address or 0), "error": "null-or-low-pointer"}
        size = min(max(1, size), MAX_MEMORY_READ)
        error = self.lldb.SBError()
        try:
            data = self.process.ReadMemory(address, size, error)
        except Exception as exc:
            return {"address": _hex(address), "size": size,
                    "error": "read-exception:%s" % type(exc).__name__}
        if not error.Success():
            return {"address": _hex(address), "size": size,
                    "error": error.GetCString() or "read-failed"}
        return {"address": _hex(address), "size": len(data), "hex": bytes(data).hex()}

    def _base_record(self, site, frame):
        pc = frame.GetPC()
        lr = self._register(frame, "lr")
        return {
            "event": site,
            "hit": self.hits[site],
            "pc": _hex(pc),
            "lr": _hex(lr or 0),
            "timestamp_ns": time.time_ns(),
        }

    def _capture(self, site, frame):
        """Per-site evidence required by the display-runtime trace plan."""
        rec = self._base_record(site, frame)
        r = lambda name: self._register(frame, name)
        regs = {}
        memory = {}

        def add(*names):
            for name in names:
                value = r(name)
                regs[name] = _hex(value or 0, 8 if name.startswith("w") else 16)

        if site == "a401":
            add("x0", "x1", "x2", "x3")
        elif site == "hotplug_entry":
            add("x0", "x1", "x2", "w3")
            x2 = r("x2")
            if x2:
                # IOMFB_TiledDisplayInfo is 0x4c bytes at x2.  Read that
                # bounded object, not an invented field at x2+0x4c.
                memory["display_info_0x4c"] = self._memory(x2, 0x4c)
        elif site in ("d575_handler", "d575_callsite"):
            # rpc_callee_gated ABI from tools/re/dcp_dtable.py: x0..x5.
            add("x0", "x1", "x2", "w3", "x4", "w5")
            x2 = r("x2")
            if site == "d575_handler" and x2:
                memory["input_0x58"] = self._memory(x2, 0x58)
        elif site in ("default_fb_public", "default_fb_gated"):
            add("x0", "w1", "w2")
        elif site == "default_local_create_return":
            add("w0", "x19")
            x19 = r("x19")
            if x19:
                memory["x19_plus_0x160"] = self._memory(x19 + 0x160, 8)
        elif site == "a482_pre":
            add("x0", "x1", "w2", "w3", "x4")
        elif site == "a482_post":
            add("w0")
        elif site == "a482_wrapper":
            add("x0", "x1", "w2", "w3", "x4")
        elif site == "surface_map_entry":
            add("x0", "x1", "x2", "x3", "w4")
        elif site == "surface_map_return":
            add("w0", "x25", "x24")
            x25, x24 = r("x25"), r("x24")
            if x25:
                memory["u64_at_x25"] = self._memory(x25, 8)
            if x24:
                memory["u64_at_x24"] = self._memory(x24, 8)
        else:  # validate_address_map prevents this, but preserve safe behavior.
            add("x0", "x1", "x2", "x3")

        rec["regs"] = regs
        if memory:
            rec["memory"] = memory
        return rec

    def handle_hit(self, site, frame, _bp_loc, _internal_dict):
        self.hits[site] += 1
        if self.emitted[site] < self.caps[site]:
            try:
                rec = self._capture(site, frame)
                self.stream.write(json.dumps(rec, sort_keys=True) + "\n")
                self.stream.flush()
                self.emitted[site] += 1
            except Exception as exc:
                # Never let a logging bug halt the boot.  The bounded error is
                # still useful evidence that the breakpoint fired.
                self.stream.write(json.dumps({
                    "event": site,
                    "hit": self.hits[site],
                    "error": "capture-exception:%s" % type(exc).__name__,
                }, sort_keys=True) + "\n")
                self.stream.flush()
                self.emitted[site] += 1
        return False  # LLDB breakpoint callback convention: auto-continue.

    def callback_error(self, site, frame, exc):
        """Make callback-dispatch failures visible without stopping the guest."""
        self.callback_errors[site] += 1
        if self.callback_errors[site] > MAX_CALLBACK_ERRORS:
            return
        try:
            pc = _hex(frame.GetPC()) if frame else None
        except Exception:
            pc = None
        rec = {
            "event": "callback_error",
            "site": site,
            "count": self.callback_errors[site],
            "error": "%s:%s" % (type(exc).__name__, str(exc)[:160]),
        }
        if pc:
            rec["pc"] = pc
        try:
            self.stream.write(json.dumps(rec, sort_keys=True) + "\n")
            self.stream.flush()
        except Exception:
            # The trace stream itself can be gone while LLDB is tearing down.
            # Do not turn that into a guest stop.
            pass

    def install(self, target):
        for site in SITE_ORDER:
            bp = target.BreakpointCreateByAddress(self.addresses[site])
            if not bp.IsValid() or bp.GetNumLocations() < 1:
                raise RuntimeError("failed to create breakpoint for %s at %s" %
                                   (site, _hex(self.addresses[site])))

            # Apple LLDB exposes SetScriptCallbackFunction, not SetCallback.
            # The callback returns False below, which is LLDB's documented
            # auto-continue result for a breakpoint script callback.
            bp_id = bp.GetID()
            _BREAKPOINT_OWNERS[bp_id] = (self, site)
            bp.SetScriptCallbackFunction(__name__ + ".lldb_breakpoint_callback")
            self.breakpoint_ids.append(bp_id)

    def clear_callbacks(self):
        for bp_id in self.breakpoint_ids:
            _BREAKPOINT_OWNERS.pop(bp_id, None)
        self.breakpoint_ids.clear()


def lldb_breakpoint_callback(frame, bp_loc, internal_dict):
    """LLDB entry point registered with every live breakpoint.

    Breakpoint IDs are unique inside this target.  Returning False even for a
    stale location guarantees a trace clean-up cannot accidentally stop XNU.
    """
    del internal_dict
    owner = None
    try:
        owner = _BREAKPOINT_OWNERS.get(bp_loc.GetBreakpoint().GetID())
        if owner is not None:
            tracer, site = owner
            return tracer.handle_hit(site, frame, bp_loc, None)
    except Exception as exc:
        if owner is not None:
            tracer, site = owner
            tracer.callback_error(site, frame, exc)
        else:
            # There is no session stream to write to if LLDB invoked a stale
            # location after clean-up.  Do not hide it completely.
            print("iomfb-calltrace callback dispatch error: %s" % exc,
                  file=sys.stderr)
    return False


def _open_output(path):
    return sys.stdout if path == "-" else open(path, "w", encoding="utf-8")


def _state_name(lldb, state):
    """Return a stable human-readable LLDB process-state name."""
    try:
        return lldb.SBDebugger.StateAsCString(state) or "unknown(%d)" % state
    except Exception:
        return "unknown(%d)" % state


def _stop_reason(process):
    """Return the selected thread's stop reason without assuming a symbol file."""
    try:
        thread = process.GetSelectedThread()
        if not thread or not thread.IsValid():
            return None
        out = {"code": thread.GetStopReason()}
        try:
            desc = thread.GetStopDescription(256)
            if desc:
                out["description"] = desc
        except Exception:
            pass
        return out
    except Exception:
        return None


def _is_terminal_state(lldb, state):
    """Only definite teardown states end a trace; Invalid is transient at attach."""
    return state in (lldb.eStateExited, lldb.eStateDetached)


def run_live(args, addresses, caps, lldb, debugger=None):
    """Attach, event-pump LLDB, trace a bounded period, then detach.

    QEMU's gdbstub reports an intermediate ``invalid``/``connected`` process
    state while it changes from -S to running.  Polling GetState alone treated
    that as an exit in the first live run.  This function pumps the debugger's
    own listener and acts only on state-change events; ``invalid`` is logged
    but never considered terminal.
    """
    error = lldb.SBError()
    owns_debugger = debugger is None
    if owns_debugger:
        debugger = lldb.SBDebugger.Create()
    debugger.SetAsync(True)
    target = debugger.CreateTargetWithFileAndArch("", "arm64")
    if not target or not target.IsValid():
        if owns_debugger:
            lldb.SBDebugger.Destroy(debugger)
        raise RuntimeError("LLDB could not create an arm64 target")

    # Use the debugger's listener, not an unpumped side listener.  ConnectRemote
    # posts eBroadcastBitStateChanged events here; waiting on it is what lets
    # LLDB dispatch script breakpoint callbacks during an asynchronous continue.
    listener = debugger.GetListener()
    process = target.ConnectRemote(listener, "connect://%s:%d" %
                                   (args.host, args.port), "gdb-remote", error)
    if not error.Success() or not process or not process.IsValid():
        if owns_debugger:
            lldb.SBDebugger.Destroy(debugger)
        raise RuntimeError("gdb-remote connect failed: %s" %
                           (error.GetCString() or "unknown error"))

    stream = _open_output(args.output)
    tracer = CallTracer(lldb, process, stream, addresses, caps)
    state_records = 0
    state_dropped = 0
    end_reason = "duration"

    def record_state(state, source, current_state=None):
        nonlocal state_records, state_dropped
        if state_records >= MAX_STATE_RECORDS:
            state_dropped += 1
            return
        rec = {
            "event": "state",
            "source": source,
            "state": _state_name(lldb, state),
        }
        if current_state is not None and current_state != state:
            rec["current_state"] = _state_name(lldb, current_state)
        if state == lldb.eStateStopped:
            reason = _stop_reason(process)
            if reason:
                rec["stop_reason"] = reason
        stream.write(json.dumps(rec, sort_keys=True) + "\n")
        stream.flush()
        state_records += 1

    def continue_guest(source):
        """Resume an explicit stopped event and leave the rationale in JSONL."""
        # Listener queues can contain the attach's initial stopped event after
        # the earlier Continue has already made the remote target running.
        # Never issue a second continue for that stale event.
        current_state = process.GetState()
        if current_state != lldb.eStateStopped:
            stream.write(json.dumps({
                "event": "continue",
                "source": source,
                "skipped": True,
                "current_state": _state_name(lldb, current_state),
            }, sort_keys=True) + "\n")
            stream.flush()
            return
        continue_error = process.Continue()
        rec = {
            "event": "continue",
            "source": source,
            "success": bool(continue_error.Success()),
        }
        if not continue_error.Success():
            rec["error"] = continue_error.GetCString() or "unknown error"
            rec["raced_running"] = "already running" in rec["error"].lower()
        stream.write(json.dumps(rec, sort_keys=True) + "\n")
        stream.flush()
        if not continue_error.Success():
            # The gdbstub can acknowledge the first Continue between the
            # GetState check above and this request.  That is the stale-stop
            # race observed with QEMU -S, not a trace failure.
            if rec["raced_running"]:
                return
            raise RuntimeError("could not continue guest: %s" % rec["error"])

    try:
        tracer.install(target)
        stream.write(json.dumps({
            "event": "session_start",
            "address_map": _json_map(addresses),
            "endpoint": "%s:%d" % (args.host, args.port),
            "site_caps": caps,
        }, sort_keys=True) + "\n")
        stream.flush()

        initial_state = process.GetState()
        record_state(initial_state, "attach")
        if initial_state in (lldb.eStateStopped, lldb.eStateCrashed):
            continue_guest("attach")

        deadline = None if args.duration == 0 else time.monotonic() + args.duration
        while deadline is None or time.monotonic() < deadline:
            event = lldb.SBEvent()
            # Apple's SBListener.WaitForEvent takes whole seconds, which is
            # too coarse for a short trace and previously rejected our float.
            # GetNextEvent is nonblocking; the short sleep preserves a
            # responsive duration clock while continuously draining the
            # debugger listener that dispatches gdb-remote state changes.
            got_event = listener.GetNextEvent(event)
            if not got_event:
                time.sleep(0.05)
                continue
            if not lldb.SBProcess.EventIsProcessEvent(event):
                continue
            state = lldb.SBProcess.GetStateFromEvent(event)
            current_state = process.GetState()
            record_state(state, "event", current_state)
            if _is_terminal_state(lldb, state):
                end_reason = _state_name(lldb, state)
                break
            # A scripted breakpoint has already returned False by the time we
            # observe its stop event.  Calling Continue defensively here also
            # handles an unrelated remote stop without leaving the VM paused.
            if state == lldb.eStateStopped:
                continue_guest("state-event")

        summary = {
            "event": "session_end",
            "a401_hits": tracer.hits["a401"],
            "emitted": {site: tracer.emitted[site] for site in SITE_ORDER},
            "hits": {site: tracer.hits[site] for site in SITE_ORDER},
            "callback_errors": {site: tracer.callback_errors[site]
                                for site in SITE_ORDER},
            "reason": end_reason,
            "state_records": state_records,
            "state_records_dropped": state_dropped,
        }
        stream.write(json.dumps(summary, sort_keys=True) + "\n")
        stream.flush()
        if tracer.hits["a401"] == 0:
            raise RuntimeError("positive control failed: A401 never hit")
        return 0
    finally:
        # Detach rather than Stop/Kill: this tool must not perturb the boot it
        # is measuring.  QEMU's gdbstub resumes a detached target.
        if process and process.IsValid():
            try:
                process.Detach()
            except Exception:
                pass
        tracer.clear_callbacks()
        if stream is not sys.stdout:
            stream.close()
        if owns_debugger:
            lldb.SBDebugger.Destroy(debugger)


def _command_result_error(result, message):
    result.SetError(message)
    # eReturnStatusFailed is present on Apple LLDB and makes `lldb -b` report
    # the trace's positive-control failure instead of a silent success.
    try:
        import lldb
        result.SetStatus(lldb.eReturnStatusFailed)
    except Exception:
        pass


def lldb_command(debugger, command, exe_ctx, result, internal_dict):
    """The ``iomfb-calltrace`` LLDB command registered at module import."""
    del exe_ctx, internal_dict
    try:
        # LLDB hands us one shell-like string.  Preserve quoted output/map
        # paths instead of assuming every workspace path lacks spaces.
        parsed = offline_main(shlex.split(command))
        if isinstance(parsed, int):
            result.PutCString("offline validation complete")
            return
        args, addresses, caps = parsed
        import lldb
        # The callback function was imported into *this* debugger's script
        # interpreter.  Creating another SBDebugger here lets gdb-remote hit
        # the address but leaves its callback name unresolved, a silent miss
        # in the first two live runs.
        run_live(args, addresses, caps, lldb, debugger=debugger)
        result.PutCString("iomfb-calltrace: A401 positive control hit")
    except Exception as exc:
        _command_result_error(result, "iomfb-calltrace: %s" % exc)


def __lldb_init_module(debugger, internal_dict):
    """Register the live command when imported by ``command script import``."""
    del internal_dict
    debugger.HandleCommand(
        "command script add -f iomfb_calltrace.lldb_command iomfb-calltrace")
    print("iomfb-calltrace registered; run `iomfb-calltrace --help`")


def main(argv=None):
    try:
        parsed = offline_main(argv)
        if isinstance(parsed, int):
            return parsed
        args, addresses, caps = parsed
        try:
            import lldb
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "live tracing needs LLDB's embedded Python; use: /usr/bin/lldb -b "
                "-o 'command script import %s' -o 'iomfb-calltrace ...'" %
                os.path.abspath(__file__)) from exc
        return run_live(args, addresses, caps, lldb)
    except (OSError, RuntimeError, ValueError) as exc:
        print("iomfb-calltrace: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
