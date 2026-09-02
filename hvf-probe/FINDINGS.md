# Can we virtualize the CPU and emulate only the Apple-specific parts?

Measured with `hvf_probe` on this Mac (macOS 27.0, 26A5421a). Raw data in
`results.txt`, tally in `results-tally.txt`.

## The short version

Nearly yes. One instruction blocks it, and that instruction has a plausible
workaround we already have the machinery for.

## What traps to us (emulatable)

At **guest EL1 in a plain VM**, every Apple IMP-DEF system register SPTM needs
exits to the VMM with `EC=0x18` (MSR/MRS/SYS trap), giving us the register
encoding, direction and target register — everything needed to emulate them:

```
SPRR_CONFIG_EL1  SPRR_PPERM_EL1  SPRR_UPERM_EL0  SPRR_AMRANGE_EL1
SPRR_PMPRR_EL1   SPRR_UMPRR_EL1
GXF_CONFIG_EL1   GXF_ENTRY_EL1   GXF_PABENTRY_EL1   CURRENTG
ASPSR_GL1        SP_GL1          ESR_GL1            ELR_GL1
```

That last group is the guarded-mode register bank. 70 of the probes in this
mode are `trap-to-vmm`.

## What does not (the blocker)

```
genter          exec   GUEST-EXC@EL1   ESR=0x02000000  EC=0x00 UNKNOWN/UNDEF
gexit           exec   GUEST-EXC@EL1   ESR=0x02000000  EC=0x00 UNKNOWN/UNDEF
```

The GXF instructions are **UNDEF to the guest** — the guest takes its own
undefined-instruction exception at its EL1 vector and the VMM never sees it. So
when SPTM executes `genter`, we get no chance to emulate the guarded-mode
switch.

Note the earlier project note that "Apple IMP-DEF regs are UNDEF at guest EL2"
is right but incomplete: it is true *at guest EL2* (102 guest-exceptions there),
and false at guest EL1 in a plain VM, where they trap to us instead. Which
exception level the guest kernel runs at decides the whole question.

## The workaround

`HVC` **does** exit to the VMM (`EXIT->VMM ESR=0x5a000000 EC=0x16`). Since we
already patch the kernelcache (`xnu_patch.c`), we could binary-patch
`genter` (`0x00201420`) and `gexit` (`0x00201400`) into `HVC`, and emulate the
guarded-mode transition in the VMM using the register state we can already trap.

## Why we are not doing it now

- **Exit rate.** SPTM sits on the page-table path, so every page operation
  becomes a VM exit. Whether that still beats TCG is unmeasured.
- **SPRR semantics are not hardware-enforced** under this scheme: page
  permissions would carry standard ARM meaning, not Apple's. Fine for an
  emulator, wrong for anything security-meaningful. Say so loudly if this ever
  ships.
- **No prior art.** Nobody has virtualized a stock SPTM-based iOS.
- **It does not serve the endgame.** On Windows ARM there are no Apple IMP-DEF
  registers at all, so that target needs full TCG regardless. This would be an
  Apple-Silicon-host-only optimisation.

Recorded so the option is costed rather than forgotten.
