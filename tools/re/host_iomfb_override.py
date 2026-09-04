"""Apply one IOMFB output override to a live QEMU process from host LLDB.

This is a diagnostic iteration aid, not a device-model implementation.  It
lets a frozen, already-booted guest test a canned reply without paying for a
new boot.  Invoke it after attaching LLDB to QEMU::

    command script import tools/re/host_iomfb_override.py
    script host_iomfb_override.apply(lldb.debugger, "A385=01")

The helper resolves the DCP ASC through QOM, obtains its DarwinIOMFB object,
and calls the same validated override parser used at QEMU startup.  The QOM
path is configurable because the unattached-device index is construction-
order dependent::

    DVM_DCP_QOM_PATH=/machine/unattached/device[8]

Only the existing ``NAME=HEXBYTES`` grammar is accepted.  An empty byte string
is valid.  This module deliberately does not resume or detach; the caller
controls the frozen guest and can install more read-only probes first.
"""

import os
import re

import lldb


_SPEC = re.compile(r"^[ -~]{4}=(?:[0-9a-fA-F]{2})*$")


def _quote_c(value):
    return (value.replace("\\", "\\\\")
                 .replace('"', '\\"'))


def apply(debugger, spec):
    if not isinstance(spec, str) or not _SPEC.fullmatch(spec):
        raise ValueError("override must be exactly NAME=HEXBYTES")
    path = os.environ.get(
        "DVM_DCP_QOM_PATH", "/machine/unattached/device[8]")
    if not path.startswith("/machine/") or any(
            character not in "/[]-_abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" for character in path):
        raise ValueError("unsafe DCP QOM path")

    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    if not process.IsValid() or process.GetState() != lldb.eStateStopped:
        raise RuntimeError("QEMU must be stopped under host LLDB")

    expression = r'''({
        bool dvm_ambiguous = false;
        Object *dvm_object = object_resolve_path("%s", &dvm_ambiguous);
        DarwinDCP *dvm_dcp = dvm_object ?
            (DarwinDCP *)((DarwinASCState *)dvm_object)->opaque : 0;
        DarwinIOMFB *dvm_iomfb = dvm_dcp ? dvm_dcp->iomfb : 0;
        if (dvm_iomfb) iomfb_parse_overrides(dvm_iomfb, "%s");
        (unsigned long long)dvm_iomfb;
    })''' % (_quote_c(path), _quote_c(spec))
    options = lldb.SBExpressionOptions()
    options.SetLanguage(lldb.eLanguageTypeC)
    options.SetIgnoreBreakpoints(True)
    options.SetTimeoutInMicroSeconds(10_000_000)
    value = target.EvaluateExpression(expression, options)
    error = value.GetError()
    if not error.Success():
        raise RuntimeError("host expression failed: %s" % error.GetCString())
    iomfb = value.GetValueAsUnsigned()
    if not iomfb:
        raise RuntimeError("DCP/IOMFB object was not found at %s" % path)
    print("HOST_IOMFB_OVERRIDE applied=1 spec=%s qom_path=%s iomfb=0x%x "
          "evidence=diagnostic-only" % (spec, path, iomfb), flush=True)
    return iomfb
