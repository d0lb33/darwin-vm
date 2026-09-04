"""Host-LLDB witness and optional one-request SKS validator override.

This debugger runs outside the guest and breaks in QEMU's own
sep_sks_validate_migrate_request().  By default it is read-only: every request
is hashed and decoded in the log, then QEMU continues.  A caller can force one
exact captured request to return true by setting all three variables:

    DVM_HOST_SKS_OVERRIDE_SHA256=<64 hex digits>
    DVM_HOST_SKS_OVERRIDE_CLASS=<decimal or 0x...>
    DVM_HOST_SKS_OVERRIDE_ONCE=1

For a request whose per-boot bytes are not known beforehand, set an approval
file plus all three structural match fields.  The callback freezes QEMU at the
matching request, writes `<approval>.challenge.json` and
`<approval>.request.bin`, and waits for the approval file to contain the exact
SHA-256 it just observed:

    DVM_HOST_SKS_APPROVAL_FILE=/tmp/dvm/TAG.sks.approve
    DVM_HOST_SKS_MATCH_SIZE=164
    DVM_HOST_SKS_MATCH_CLASS=3
    DVM_HOST_SKS_MATCH_KIND=4

The hash guard is intentional.  This is a same-boot diagnostic, never evidence
that the device-model implementation is correct; a subsequent unmodified boot
is still required.
"""

import hashlib
import json
import os
import struct
import time

import lldb


_SYMBOL = "sep_sks_validate_migrate_request"
_seen = 0
_overridden = False


def _reg_u64(frame, name):
    value = frame.FindRegister(name)
    return value.GetValueAsUnsigned() if value.IsValid() else 0


def _u32(data, offset):
    if offset + 4 > len(data):
        return None
    return int.from_bytes(data[offset : offset + 4], "little")


def _env_int(name):
    value = os.environ.get(name, "")
    if not value:
        return None
    try:
        return int(value, 0)
    except ValueError:
        return None


def _approval_digest(data, digest, fields, request_size):
    approval = os.environ.get("DVM_HOST_SKS_APPROVAL_FILE", "")
    if not approval:
        return None
    if not approval.startswith("/tmp/dvm/") or ".." in approval.split("/"):
        print("HOST_SKS_APPROVAL refused=unsafe-path", flush=True)
        return None

    match_size = _env_int("DVM_HOST_SKS_MATCH_SIZE")
    match_class = _env_int("DVM_HOST_SKS_MATCH_CLASS")
    match_kind = _env_int("DVM_HOST_SKS_MATCH_KIND")
    if None in (match_size, match_class, match_kind):
        print("HOST_SKS_APPROVAL refused=incomplete-match", flush=True)
        return None
    if (request_size, fields["class"], fields["kind"]) != (
        match_size,
        match_class,
        match_kind,
    ):
        return None

    challenge_path = approval + ".challenge.json"
    request_path = approval + ".request.bin"
    temporary = challenge_path + ".tmp"
    with open(request_path, "wb") as stream:
        stream.write(data)
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(
            {
                "sha256": digest,
                "request_path": request_path,
                "request_size": request_size,
                "fields": fields,
            },
            stream,
            sort_keys=True,
        )
        stream.write("\n")
    os.replace(temporary, challenge_path)

    try:
        timeout = float(os.environ.get("DVM_HOST_SKS_APPROVAL_TIMEOUT", "300"))
    except ValueError:
        timeout = 300.0
    timeout = max(1.0, min(timeout, 3600.0))
    print(
        "HOST_SKS_AWAIT_APPROVAL "
        f"sha256={digest} approval={approval} timeout={timeout:.1f}",
        flush=True,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(approval, "r", encoding="ascii") as stream:
                approved = stream.read().strip().lower()
        except OSError:
            approved = ""
        if approved == digest:
            print(f"HOST_SKS_APPROVAL accepted sha256={digest}", flush=True)
            return digest
        time.sleep(0.1)
    print(f"HOST_SKS_APPROVAL timed-out sha256={digest}", flush=True)
    return None


def _callback(frame, _bp_loc, _internal_dict):
    global _seen, _overridden

    _seen += 1
    process = frame.GetThread().GetProcess()
    request_ptr = _reg_u64(frame, "x1")
    request_size = _reg_u64(frame, "w2")
    response_class_ptr = _reg_u64(frame, "x3")
    error = lldb.SBError()

    if request_size == 0 or request_size > 4096 or request_ptr == 0:
        print(
            "HOST_SKS_REQUEST "
            f"count={_seen} invalid-pointer=0x{request_ptr:x} "
            f"size={request_size}",
            flush=True,
        )
        return False

    data = process.ReadMemory(request_ptr, request_size, error)
    if not error.Success() or len(data) != request_size:
        print(
            "HOST_SKS_REQUEST "
            f"count={_seen} read-error={error.GetCString()} "
            f"pointer=0x{request_ptr:x} size={request_size}",
            flush=True,
        )
        return False

    digest = hashlib.sha256(data).hexdigest()
    fields = {
        "header": _u32(data, 0x00),
        "version": _u32(data, 0x14),
        "variant": _u32(data, 0x4C),
        "kind": _u32(data, 0x68),
        "class": _u32(data, 0x6C),
        "record_len": _u32(data, 0x84 if request_size == 0xA4 else 0x78),
    }
    print(
        "HOST_SKS_REQUEST "
        f"count={_seen} monotonic={time.monotonic():.6f} "
        f"thread=0x{frame.GetThread().GetThreadID():x} "
        f"pointer=0x{request_ptr:x} size={request_size} sha256={digest} "
        + " ".join(f"{key}={value}" for key, value in fields.items()),
        flush=True,
    )

    expected = os.environ.get("DVM_HOST_SKS_OVERRIDE_SHA256", "").lower()
    once = os.environ.get("DVM_HOST_SKS_OVERRIDE_ONCE", "") == "1"
    class_text = os.environ.get("DVM_HOST_SKS_OVERRIDE_CLASS", "")
    approval_configured = bool(os.environ.get("DVM_HOST_SKS_APPROVAL_FILE", ""))
    if not expected and not class_text and not once and not approval_configured:
        return False
    if expected and (
        len(expected) != 64 or
        any(c not in "0123456789abcdef" for c in expected)
    ):
        print("HOST_SKS_OVERRIDE refused=invalid-sha256", flush=True)
        return False
    try:
        response_class = int(class_text, 0)
    except ValueError:
        print("HOST_SKS_OVERRIDE refused=invalid-class", flush=True)
        return False
    if not once:
        print("HOST_SKS_OVERRIDE refused=once-guard-not-set", flush=True)
        return False
    if _overridden:
        return False
    if approval_configured:
        expected = _approval_digest(data, digest, fields, request_size)
        if expected is None:
            return False
    if digest != expected:
        return False
    if not response_class_ptr or not 0 <= response_class <= 0xFFFFFFFF:
        print("HOST_SKS_OVERRIDE refused=invalid-output", flush=True)
        return False

    wrote = process.WriteMemory(
        response_class_ptr, struct.pack("<I", response_class), error
    )
    if not error.Success() or wrote != 4:
        print(
            f"HOST_SKS_OVERRIDE refused=write-failed error={error.GetCString()}",
            flush=True,
        )
        return False

    return_value = frame.EvaluateExpression("(bool)true")
    return_error = frame.GetThread().ReturnFromFrame(frame, return_value)
    if not return_error.Success():
        print(
            f"HOST_SKS_OVERRIDE refused=return-failed error={return_error.GetCString()}",
            flush=True,
        )
        return False

    _overridden = True
    print(
        "HOST_SKS_OVERRIDE "
        f"applied=1 sha256={digest} response_class={response_class} "
        "evidence=diagnostic-only",
        flush=True,
    )
    return False


def install(debugger):
    target = debugger.GetSelectedTarget()
    breakpoint = target.BreakpointCreateByName(_SYMBOL)
    breakpoint.SetScriptCallbackFunction(
        "host_sks_request_callbacks._callback"
    )
    print(
        "HOST_SKS_INSTALL "
        f"symbol={_SYMBOL} locations={breakpoint.GetNumLocations()} "
        f"breakpoint_id={breakpoint.GetID()}",
        flush=True,
    )
    if breakpoint.GetNumLocations() != 1:
        raise RuntimeError(
            f"expected one {_SYMBOL} location, got {breakpoint.GetNumLocations()}"
        )
