#!/usr/bin/env python3
"""dt_fixup.py with the one-line ANS fix applied, until dt_fixup.py carries it.

    python3 tools/ans/dt_ans_fixup.py /tmp/dvm/dtree_raw out.bin \
        -nvram nvram.bin -enable ans [-enable smc ...]

Takes exactly the arguments dt_fixup.py takes and produces the same tree, with
`fixup_iops()`'s skip test corrected so that /arm-io/ans/iop-ans-nub gets the
pre-loaded firmware region RTBuddy needs.

WHY
---
`dt_fixup.py -enable ans` alone gets the ANS drivers to *match* but never gets
the coprocessor to *start*.  The boot stops with RTBuddy(ANS2) registered and
`AppleANS3CGv2Controller::probe` scoring 500000, and then nothing: no endpoint
is ever opened, so `AppleANS2NVMeController::start()` parks forever in

    IOService::waitForMatchingService("ANS2Endpoint1", /*timeout=*/-1)

(IONVMeFamily+0xa9a4; the REQUIRE at line 367 is "ANS2Endpoint1 didn't show
up") and not one NVMe register is ever touched.  Measured as the difference
between two probe runs of the same binary:

    A9DBG (plain -enable ans): last ANS line is
        "Registering: ../AppleASCWrapV6/iop-ans-nub/RTBuddy(ANS2)/RTBuddyService"
        and DARWIN_ASC_DEBUG=1 logs *zero* asc(ANS2) mailbox messages.
    A9FW  (this fix):
        "Registering: ../AppleASCWrapV6/iop-ans-nub/RTBuddy(ANS2)/ANS2Endpoint1"
        "AppleANS2NVMeController::start():435: Found the ANS2Endpoint1"
        "IONVMeController::start():844: Successfully initialized NVMe drive"
        "Registering: ../APPLE SSD (darwin-ans) Media/IOMediaBSDClient"
        "nx_mount:1509: disk0 stable checkpoint indices: desc 108 data 2344"

The cause is one token in `dt_fixup.py:287`:

    if 'rtbuddy' not in compat or 'region-base' in nub.props:
        continue

`fixup_iops()` deliberately skips nubs that already carry a `region-base`,
because iBoot describes a real firmware region for some IOPs and overwriting
one with the DCP's invented address made SPTM kill the boot
(VIOLATION_FRAME_TYPE ... fte->type(XNU_KERNEL_RESTRICTED)).  Right for SMC,
wrong for ANS, because the two nubs do not ship the same thing.  Straight out
of `ipsw_db/24A5430a__iPhone17,3/DeviceTree.d47ap.im4p`:

    /arm-io/smc/iop-smc-nub   region-base = 0x30de00000  region-size = 0x100000
                              pre-loaded  = 0            firmware-name = t8140smc
    /arm-io/ans/iop-ans-nub   region-base = 0x0          region-size = 0x0
                              (no pre-loaded, no firmware-name)

SMC ships a real region.  ANS ships a **zero placeholder** for iBoot to fill
in, and `'region-base' in nub.props` cannot tell the two apart, so ANS is
skipped and gets neither `pre-loaded` nor a usable region.  Note this does not
reintroduce the overwrite that caused the SPTM violation: SMC's real
0x30de00000 is still left alone, because only a *zero* region-base is treated
as absent.

THE FIX for dt_fixup.py, which is what this script monkeypatches in:

    -    if 'rtbuddy' not in compat or 'region-base' in nub.props:
    +    # A zero region-base is iBoot's placeholder, not a real region: the ANS
    +    # nub ships region-base = region-size = 0, while SMC's is a real
    +    # address (0x30de00000) with a firmware-name beside it. Skipping on the
    +    # key alone leaves ANS with no firmware and RTBuddy never starts it.
    +    if 'rtbuddy' not in compat or nub.props.get('region-base') not in (None, 'u64:0x0'):
             continue

`pre-loaded` on its own is not enough: with `region-base` still 0 the boot
panics instead of stalling, which is the misleading

    panic: RTBuddy::_attemptFirmwareLoad():346 REQUIRE failed  @RTBuddy.cpp:346

that earlier runs hit (probe tag ANSPRE).  Both properties are required.

WHY THIS IS A WRAPPER AND NOT A POST-PROCESSOR
----------------------------------------------
Decoding and re-encoding an already-fixed-up tree is lossy: `fixup()` rewrites
/chosen/random-seed as 256 bytes of b'A', which `is_probably_a_string()` then
reads back as a *string* and re-encodes with a NUL terminator.  The tree grows
to 257 bytes there and SPTM refuses it:

    SPTM PANIC: ed: random-seed (0xfffffff006fb3334) size mismatch (257) or NULL

So this runs dt_fixup's own single pass over the raw tree instead.

Delete this script once dt_fixup.py carries the fix above.
"""
import importlib.util
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_dt_fixup():
    path = os.path.join(REPO, 'dt_fixup.py')
    spec = importlib.util.spec_from_file_location('dt_fixup', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    dtf = load_dt_fixup()
    original = dtf.fixup_iops

    def fixup_iops(d):
        # Run the real one first so every other IOP is treated exactly as
        # before, then fill in the nubs it skipped only because their
        # region-base is a zero placeholder.
        original(d)
        for c in d['arm-io'].children:
            if 'compatible' not in c.props:
                continue
            for nub in c.children:
                compat = nub.props.get('compatible', '')
                if isinstance(compat, bytes):
                    compat = compat.decode('utf8', 'replace')
                if 'rtbuddy' not in compat:
                    continue
                if nub.props.get('region-base') not in (None, 'u64:0x0'):
                    continue        # a real iBoot-supplied region; leave it
                if 'pre-loaded' in nub.props and nub.props.get('region-base') != 'u64:0x0':
                    continue        # already handled by the original pass
                nub.props['pre-loaded'] = "u32:1"
                nub.props['region-base'] = "u64:0x10010000000"
                nub.props['region-size'] = "u64:0x100000"
                nub.props['no-firmware-service'] = "<NULL>"
                sys.stderr.write(
                    "dt_ans_fixup: %s had a zero region-base placeholder; gave it "
                    "pre-loaded + region-base 0x10010000000\n" % nub.props.get('name'))

    dtf.fixup_iops = fixup_iops
    dtf.main()


if __name__ == '__main__':
    main()
