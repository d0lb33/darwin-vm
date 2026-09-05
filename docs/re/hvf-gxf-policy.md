# GXF internal-ISA VM policy and the next protection stage

September 5, 2026. The native boot still stops before GXF_CONFIG_EL2=`0x6f`
at runtime `0xfffffff0070a3978`. This investigation identifies the input
controlling bit 6 and captures the subsequent firmware sequence under TCG.
It does not establish the complete GXF hardware contract or advance HVF.

## Firmware policy input

`HVF_GXF_CONFIG_FLAG1/results.json` records an actual TCG write watchpoint
on runtime `0xfffffff0070921e0`. The CPU stops at `0xfffffff0070b29e8`,
before `STRB W8,[X9,#0x1e0]`, with W8=0 and X9=`0xfffffff007092000`.
The original firmware addresses below have the `0xfffffff027` prefix;
the loaded image uses `0xfffffff007`.

Disassembly of original `0xfffffff0270b28fc..29e8` identifies this decision:

1. Look up `/product` (`0xfffffff027012811`), then the property
   `internal-isa-vm-allowed` (`0xfffffff027006d83`). A successful property
   lookup sets the flag to 1 at `...b2948`; this path checks presence.
2. Otherwise look up `/chosen/asmb` (`0xfffffff027006d9b`), then `lp-sip0`
   (`0xfffffff027006da8`). Require a successful lookup and length 8.
3. Load its low 16 bits, test `0x1010` at `...b29b8..29c4`, and set the flag
   if either tested bit is set. Missing/invalid properties produce zero.
4. Store the result at `...b29e8` into the global byte described above.

Apple's [csr.h](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/csr.h)
names bit 4 CSR_ALLOW_APPLE_INTERNAL and bit 12 CSR_ALLOW_RESEARCH_GUESTS.
These match the observed mask. This is stronger evidence than expanding
the field abbreviation HVAC by guesswork.

Original `...a3954..3978` starts with configuration `0x2f`. When the flag is
zero, it clears lower GXF CONFIG/ENTRY/PABENTRY and adds `0x40`, producing
`0x6f`; when the flag is nonzero, it skips those operations. The M5 field
dump calls bit 6 HVAC. Therefore bit 6 is connected to the firmware policy
for permitting internal-ISA VMs. Which accesses it traps or rejects, its
interaction with guarded callers, and exact exception syndromes remain
unverified. Do not infer those details from the policy's name alone.

The guest input was not modified to select the permissive branch. No fault
was skipped and no register was changed through the debugger.

## Read-only host observation

`tools/perf/native_host_policy.py --out /tmp/dvm/HVF_GXF_HOST_POLICY2.json`
calls `csr_get_active_config` and queries only the presence of the relevant
property in the IODeviceTree product node. The recorded result is:

- CSR call succeeded; active configuration is 0, so neither tested bit is set.
- One product node was found; `internal-isa-vm-allowed` is absent.

This is consistent with the protected-register access failures in the
earlier public-HVF probes. It does not read the live host GXF register or
prove that HVAC alone caused those failures. It also does not establish
that changing a policy bit would grant the restricted vmapple entitlement.
No host policy, entitlement, boot configuration, or display process changed.

## What follows the current native stop

`tools/perf/native_sptm_trace.py` launches its own one-vCPU TCG process with
unmodified SPTM, uses debugger break/watchpoints and single stepping, and
records instruction words plus changed registers. It issues no debugger
register or memory writes and attaches no persistent disk. All addresses
in the JSON trace are runtime addresses.

`HVF_GXF_CONFIG_TRACE1` captures 512 instructions from the current gate.
`HVF_GXF_CONFIG_TRACE2` extends this to 4096 instructions, including 651
distinct PCs. Both complete successfully and terminate their own process.
Their binary hash is
`dc2d14c93387cb02e3af8dc493132fdd08c1c2987c87a1bce68e84f91164448a`;
original SPTM hash is
`b0fd274d3009ccfbc9902e99ef441a2231fb852931f8b31233e9bc0f55207048`.

The configuration write returns to `...b39f4`, then performs a device-tree
property lookup for `slide`. The first subsequent system-register write
is CTXR_A_CTL at `...bbcec`, trace index 1666 (zero-based). Through `...bbd98`,
SPTM activates, configures, and locks four execution-range controls:

| Bank | Initial control | Configured control | Locked control |
| --- | --- | --- | --- |
| A | `0x4000000000000000` | `0x4000000000aa019a` | `0xc000000000aa019a` |
| B | `0x4000000000000000` | `0x40000000009a02aa` | `0xc0000000009a02aa` |
| C | `0x4000000000000000` | `0x4000000000aa026a` | `0xc000000000aa026a` |
| D | `0x4000000000000000` | `0x4000000000aa02a9` | `0xc000000000aa02a9` |

The actual register encodings are `[3,0,11,5,2..5]`. TLBI VMALLE1NXS and
DSB NSHNXS occur between programming phases (`...bbd00/04`, `...bbd50/54`).
The current HVF backend accepts inactive range-bound programming only;
these control writes are further implementation work, not already enforced.
The TCG trace cannot serve as proof that TCG enforces the CTXR controls.

By the end of the trace the firmware is in a SIMD memory-zeroing loop at
`...a3ca0..3cb0`, still in GL2. This is useful future native-workload evidence,
not a boot-time measurement: debugger single stepping changes timing.

## Reproduction and limits

```sh
python3 tools/perf/native_sptm_trace.py \
  --out /tmp/dvm/UNIQUE_GXF_TRACE \
  --dtree /tmp/dvm/NATIVE_DRAM_LOW1.dtree \
  --pc 0xfffffff0070a3978 --steps 4096

python3 tools/perf/native_sptm_trace.py \
  --out /tmp/dvm/UNIQUE_GXF_POLICY \
  --dtree /tmp/dvm/NATIVE_DRAM_LOW1.dtree \
  --watch-write 0x8070921e0 --watch-write 0xfffffff0070921e0 --steps 32
```

`HVF_GXF_TRACE_TIMEOUT1` is an intentional unreachable-breakpoint control:
it records a timeout, zero traced instructions, failure status, and cleanup
of the owned process. It must not be reported as a passing trace.
Host regressions pass 24/24 in `HVF_GXF_TRACE_HOST1.log`; both new scripts
compile. No QEMU source changed or rebuild was performed in this investigation.

The Apple/Arm applications
[US20260079855A1](https://patents.google.com/patent/US20260079855A1/en) and
[US20260080087A1](https://patents.google.com/patent/US20260080087A1/en) describe
mode-dependent exception-vector selection and agent-transition restrictions.
They provide related architectural concepts, but no verified mapping to
GXF_CONFIG's PEX2/PEX0/LOCK/NACC/HVAC fields. They are not a register manual.
The M5 field gist has no comments supplying those missing semantics.

A separate m1n1-capable machine could provide a hardware oracle. Useful
experiments would compare exceptions, GENTER/GEXIT, and system-register
accesses across ordinary/guarded EL2 and lower levels, varying one control
at a time. Lock tests need an isolated boot and must come last. This Mac's
running display instance must not be used for a reboot-based experiment.
Until then, preserve the current rejection instead of replacing it with
Asahi's enable-only configuration handler.
