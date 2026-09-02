# Can Hypervisor.framework run iOS's CPU natively? Measured: no.

**Verdict: do not build it.** Two numbers, both measured on this machine:

1. **A boot to a root shell executes 1,721,436 events that would each be a VM
   exit.** At the exit cost measured on this Mac (**720 ns**, not the assumed
   1–3 µs) that is **1.24 s of pure exit overhead against a 5.41 s TCG boot**.
   Best case the whole scheme is worth **2–3x**, not 5–20x.
2. **The guest runs at EL2, and at guest EL2 Hypervisor.framework gives us
   nothing to hook.** `PSTATE=...4033c8 ---- EL2t` with PC in the kernelcache;
   all 59,766 `genter`s and 99.9% of the Apple IMP-DEF register traffic are at
   EL2. `hvf-probe/results.txt` line 140ff: at guest EL2 the Apple IMP-DEF
   registers are plain UNDEF, `genter`/`gexit` are UNDEF, and **`HVC` is taken
   by the guest, not the VMM** — so the patch-`genter`-to-`HVC` workaround has
   nothing to land on either.

The brief's hypothesis — that the `genter` rate might erase the benefit — is
wrong, but in an instructive way: `genter`+`gexit` is only **6.9%** of the exit
budget. **88.8% is Apple IMP-DEF system-register traffic**, and 46% of *that*
is a single register, `TPIDR_GL2`.

---

## Host and guest

| | |
|---|---|
| Host | `Mac17,7`, Apple M5 Max (T6050), 18 cores, 128 GiB |
| Host OS | macOS 27.0, build `26A5421a`, `xnu-13432.1.9~3/RELEASE_ARM64_T6050` |
| Guest | iPhone17,3 (`t8140`), iOS `24A5430a`, restore ramdisk, 8 GiB |
| Emulator | `qemu-sptm` at `d1530248` + the `gxfstat` counters described below |
| Branch | `worktree-agent-a08a2257dfdcb6ec5` |
| TCG path | unaffected: `tools/probe.sh --dtree /tmp/dvm/dt_base.bin --secs 60` → `xnu panics: 0`, `reached shell: yes` |

**T6050 is not t8140.** Every register semantic quoted from the host is the M5
Max's, and Apple's IMP-DEF encodings are not guaranteed stable across
generations. What the host measurements are used for here is *cost* (ns per
exit) and *trap behaviour of Hypervisor.framework*, both of which are properties
of the host and the framework, not of the emulated SoC.

---

## 1. What was instrumented, and does it distort the baseline?

`qemu-sptm/target/arm/gxfstat.c` and `include/xnu/gxfstat.h` count, per boot,
every guest event that becomes a VM exit under the proposed scheme:

| Counter | Hook | Why it is an exit under HVF |
|---|---|---|
| `genter` | `target/arm/helper.c`, `case EXCP_GENTER:` | UNDEF to an HVF guest, so it must be patched to a trapping instruction |
| `gexit` | `target/arm/tcg/translate-gxf.c`, `HELPER(gexit)` | same |
| `sysrd`/`syswr` | generated accessors in `hw/arm/apple_regs_autogen.h` (from `scripts/darwin/dumpregs.py`) | Apple IMP-DEF sysregs trap with `EC=0x18` at guest EL1 |
| `mmiord`/`mmiowr` | `accel/tcg/cputlb.c`, `do_ld_mmio_beN` / `do_st_mmio_leN` | CPU-initiated MMIO exits under HVF too |
| `exc[]` | `target/arm/helper.c`, `arm_cpu_do_interrupt_aarch64()` | informational — most stay inside the guest |

Everything is also broken down by the EL the guest was at, because that turns
out to be the whole answer.

The banked/CPU-state registers keep their `.fieldoffset` and gain a
`.readfn`/`.writefn` pair, so raw and migration access is unchanged and only
*guest* access is made visible. QEMU already ends the TB on every sysreg write
(`translate-a64.c` `handle_sys()`, `need_exit_tb = true` unconditionally for
writes), so adding a `writefn` does not change TB shape; only reads gain a
helper call.

**Distortion check (measured, not assumed):**

```
tools/time_boot.py --repeat 3                                    # instrumented
tools/time_boot.py --repeat 3 --qemu <shared prebuilt, same commit>
```

| build | run 0 | run 1 | run 2 | mean |
|---|---|---|---|---|
| instrumented | 5.47 | 5.32 | 5.52 | **5.44 s** |
| unmodified | 5.41 | 5.37 | 5.44 | **5.41 s** |

0.6% — inside run-to-run noise. The counters are trustworthy.

## 2. The measurement

```
tools/probe.sh --dtree /tmp/dvm/dt_base.bin --secs 90 --tag GXFCOUNT
grep -a '^gxfstat' /tmp/dvm/probe/GXFCOUNT.stderr.log
```

`gxfstat` prints one line per second. The boot reaches `/bin/sh: 0: can't
access tty` at ~5.4 s and the counters flatten immediately after, which is what
lets boot be separated from idle:

```
t=4.023 dgenter=31026 dsysrd=631503
t=5.033 dgenter=13682 dsysrd=299740
t=6.043 dgenter=4     dsysrd=693      <- boot over, guest idling
t=7.047 dgenter=0     dsysrd=585
```

Snapshot at `t=6.043` (boot complete, ~0.5 s of idle included):

```
gxfstat tick t=6.043 genter=59766 gexit=59766 sysrd=1253767 syswr=274635
  mmiord=24650 mmiowr=48852
  genterEL=0/0/59766/0 gexitEL=0/0/59766/0 sysEL=6153/0/1522249/0
  exc: e2=8873 e3=455 e4=2752 e6=470 e30=59766
```

(`exc` indices are `EXCP_*` from `target/arm/cpu.h`: e2=SWI, e3=PREFETCH_ABORT,
e4=DATA_ABORT, e6=FIQ, e30=GENTER.)

### The exit budget for one boot to a root shell

| category | count | share |
|---|---:|---:|
| `genter` + `gexit` | 119,532 | 6.9% |
| Apple IMP-DEF sysreg read+write | 1,528,402 | **88.8%** |
| CPU-initiated MMIO | 73,502 | 4.3% |
| **total** | **1,721,436** | |

### Rates

Guest instructions were counted with QEMU's own TCG plugin:

```
tools/probe.sh --dtree /tmp/dvm/dt_base.bin --secs 12 --tag GXFINSN12 -- \
  -plugin qemu-sptm/build/tests/tcg/plugins/libinsn.dylib,inline=on \
  -d plugin -D /tmp/dvm/probe/GXFINSN12.plugin.log
```

12 s run: 4,646,087,684 instructions, boot done at t=8.038 (the plugin slows
the boot). 45 s run: 16,688,702,829. Subtracting the idle rate
(366.9 M insn/s) gives:

- **guest instructions to root shell: 3.176 G**
- TCG throughput during boot: 3.176 G / 5.41 s = **587 M insn/s**
- `genter` rate: 59,766 / 5.41 s = **11,047 per host-second**, or
  **13,370 per guest-second** of the guest's own clock (launchd stamps the
  shell prompt at uptime `00:00:04.469217`)
- Apple sysreg trap rate: **283,000 per second**

## 3. What a VM exit actually costs on this Mac

Assuming "1–3 µs" would have been the weakest link in the argument, so it was
measured. `hvf-probe/hvf_exitbench.c` (guest vCPU at EL1 in a plain VM, MMU
off, code fetched straight out of the `hv_vm_map`'d region):

```
cd hvf-probe && make exitbench && ./hvf_exitbench 500000
```

```
convention: after HVC  ESR=0x5a000000 EC=0x16 PC=0x40000004 (PC already advanced)
convention: after MRS  ESR=0x6231bc03 EC=0x18 Rt=0 PC=0x40000100 (PC left on the MRS)
native    : 503,316,482 guest instructions, no exits   = 7412-8403 M insn/s
hvc-bare  : 500000 exits, re-entered with no reg access =  712-734 ns per exit
hvc-pcadv : + get PC / set PC                           =  693-723 ns per exit
mrs-full  : MRS S3_6_c15_c1_0, Rt decoded from the
            syndrome, destination reg written, PC advanced = 703-760 ns per exit
in-guest  : 1,048,576 exceptions taken and returned
            entirely inside the guest, no VM exit          =   21-24 ns each
```

(three runs; `mrs-full` is exactly the work the VMM would do for each of the
1.53 M Apple sysreg accesses.)

Three things fall out of this:

- **A VM exit costs ~720 ns**, better than the assumed 1–3 µs. The register
  get/set is nearly free; the exit/entry transition is the whole cost.
- **An exception handled inside the guest costs ~22 ns** — 33x cheaper. This
  matters for §6.
- **Native execution is ~8 G insn/s on a tight ALU loop.** That is an upper
  bound on native speed, not a measurement of real kernel code. Against the
  587 M insn/s this emulator sustains through the boot (§2), it puts the raw
  TCG-vs-native ceiling at ~14x — consistent with the "5–20x" the brief
  assumed, and irrelevant once exits are added.

## 4. The arithmetic

Exit overhead for one boot:

```
1,721,436 exits x  720 ns  =  1.24 s     (measured exit cost)
1,721,436 exits x 1000 ns  =  1.72 s
1,721,436 exits x 3000 ns  =  5.16 s     (would be a total wash)
```

Total projected HVF boot time = native execution of 3.176 G instructions, plus
1.24 s of exits:

| assumed native rate | exec | exits | total | vs TCG 5.41 s |
|---|---:|---:|---:|---|
| 8.1 G insn/s (microbenchmark, upper bound) | 0.39 s | 1.24 s | 1.63 s | **3.3x** |
| 4.0 G insn/s (IPC ~1 at 4 GHz) | 0.79 s | 1.24 s | 2.03 s | **2.7x** |
| 2.0 G insn/s (memory-bound OS boot) | 1.59 s | 1.24 s | 2.83 s | **1.9x** |

*Measured*: the exit count, the exit cost, the instruction count, the TCG time.
*Inferred*: the native execution rate for real kernel code, hence the range.

Even the optimistic row is 3.3x, and it ignores stage-2 faults populating guest
RAM, vtimer and interrupt-injection exits, and WFI. **The realistic ceiling is
2–3x, from a project that would take weeks.** That alone is a thin case. What
closes it is §5.

## 5. The blocker: the guest runs at EL2

`hvf-probe/FINDINGS.md` established that the Apple IMP-DEF registers trap to
the VMM (`EC=0x18`) *at guest EL1*, and are plain UNDEF *at guest EL2*. It did
not say which of those darwin-vm is. It is EL2, and that is fatal.

**Evidence 1 — PSTATE, read from the monitor with the guest frozen in the
kernelcache:**

```
tools/probe.sh --dtree /tmp/dvm/dt_base.bin --secs 12 --tag GXFEL2 --keep
python3 tools/hmp.py /tmp/dvm/GXFEL2.sock "info registers"

PC=fffffff02ab218bc ...
PSTATE=00000000004033c8 ---- EL2t
```

**Evidence 2 — every GXF transition is at EL2.** `genterEL=0/0/59766/0` and
`gexitEL=0/0/59766/0`: index 2 is EL2, and EL1 is zero. 59,766 of 59,766.

**Evidence 3 — the register traffic is in the EL2/GL2 banks.** The per-register
histogram (`gxfstat reg` lines, from the same instrumentation) for a 12 s boot,
1,536,190 accesses total:

```
gxfstat reg TPIDR_GL2                rd=707676 wr=1        46.1%
gxfstat reg CURRENTG                 rd=262398 wr=0        17.1%
gxfstat reg SPSR_GL2                 rd=63990  wr=63985
gxfstat reg ELR_GL2                  rd=61928  wr=61923
gxfstat reg ASPSR_GL2                rd=59851  wr=59848
gxfstat reg APCTL_EL2                rd=39271  wr=41244
gxfstat reg SPRR_PPERM_EL2           rd=27310  wr=27791
gxfstat reg SPRR_UPERM_EL0           rd=8424   wr=5786
gxfstat reg ESR_GL2                  rd=8276   wr=0
gxfstat reg AGTCNTRDIR_EL2           rd=4216   wr=3986
...
gxfstat reg: 251 distinct Apple IMP-DEF registers touched
```

80.8% of all accesses are to `_EL2` / `_GL2` banked forms; another 17.1% is
`CURRENTG`, the GXF state register. `sysEL=6153/0/1522249/0` — **zero** Apple
IMP-DEF accesses at EL1. The `SPSR_GL2` / `ELR_GL2` / `ASPSR_GL2` counts
(~60 k each) track the `genter` count exactly, which is what a guarded-mode
save/restore looks like.

This is consistent with `hw/arm/darwin.c:278`: *"SPTM requires EL2, and non-SPTM
XNU can run in either EL1 or EL2, so just always use EL2"*. On an SPTM device
both SPTM and the XNU kernel live at EL2/GL2.

**Why EL2 kills it.** From `hvf-probe/results.txt`, mode *"EL2 in an
EL2-enabled (nested-virt) VM"* (line 120ff), every one of 102 probes:

```
SPRR_CONFIG_EL1  S3_6_C15_C1_0  read   GUEST-EXC@EL2  ESR=0x02000000 EC=0x00 UNDEF
GXF_CONFIG_EL2   S3_6_C15_C1_4  read   GUEST-EXC@EL2  ESR=0x02000000 EC=0x00 UNDEF
genter                          exec   GUEST-EXC@EL2  ESR=0x02000000 EC=0x00 UNDEF
gexit                           exec   GUEST-EXC@EL2  ESR=0x02000000 EC=0x00 UNDEF
HVC #0                          exec   GUEST-EXC@EL2  ESR=0x5a000000 EC=0x16 HVC64
SMC #0                          exec   EXIT->VMM      ESR=0x5e000000 EC=0x17 SMC64
WFI                             exec   EXIT->VMM      ESR=0x07e00000 EC=0x01 WFx
```

At guest EL2 only `SMC` and `WFI` reach the VMM. So:

- The registers do not trap — they UNDEF into SPTM's own EL2 vector.
- `genter`/`gexit` do not trap.
- **`HVC` does not trap either.** The one workaround the brief proposed depends
  on `HVC` exiting, which is true at guest EL1 and false at guest EL2.

The only instruction left is `SMC`. Patching to `SMC` instead of `HVC` would
mean patching, pattern-located, every site that touches an Apple IMP-DEF
register, not just the GXF ones. Static site counts (op0=3, CRn ∈ {11,15},
which in AArch64 is entirely IMPLEMENTATION DEFINED space):

| binary | IMP-DEF MRS/MSR sites | `genter` | `gexit` |
|---|---:|---:|---:|
| `firmware/bootkc` | 2,650 | 174 | 0 |
| `firmware/sptm` | 998 | 1 | 6 |
| `firmware/txm` | 0 | 0 | 0 |

3,648 sysreg sites plus 181 GXF sites, in signed binaries, patched by pattern —
against the three patches in `xnu_patch.c` today. And it would still be an
~720 ns exit each, i.e. the §4 arithmetic unchanged at best.

## 6. The one architecture that could work, and why it is a port not a patch

`hvf-probe/results.txt` line 446, mode *"EL1 under guest EL2 (nested-virt VM,
HCR_EL2.VM=1, TIDCP=1)"*:

```
SPRR_CONFIG_EL1  S3_6_C15_C1_0  read  GUEST-EXC@EL2  ESR=0x6231bc03 EC=0x18
                                       MSR/MRS/SYS trap S3_6_C15_C1_0 read -> x0
GXF_CONFIG_EL1   S3_6_C15_C1_2  read  GUEST-EXC@EL2  ESR=0x6235bc03 EC=0x18
HID0             S3_0_C15_C0_0  read  GUEST-EXC@EL2  ESR=0x62303c01 EC=0x18
```

With nested virtualisation and a guest hypervisor that sets `HCR_EL2.TIDCP=1`,
Apple IMP-DEF accesses from **guest EL1** trap to **guest EL2** with full
syndrome — *inside the guest*, no VM exit. At the measured **22 ns** per
in-guest exception instead of 720 ns, the whole 1.72 M-event budget costs
**0.038 s instead of 1.24 s**: the overhead stops mattering, and the scheme
becomes worth 6–13x (0.43–0.83 s projected boot against TCG's 5.41 s) rather
than 2–3x.

The shape would be: HVF nested VM; a shim we write at guest EL2 that emulates
the Apple IMP-DEF registers and the GXF transitions; SPTM and XNU at guest EL1.

It does not work, for two reasons:

1. **SPTM and XNU cannot run at EL1.** They use the `_EL2`/`_GL2` register
   forms (80.8% of measured traffic), own stage 2, and lock VMSA at EL2.
   Moving them to EL1 is a port of both, not a patch of either.
2. **HVF crashes on the path.** Same file, modes at lines 242 and 344
   (`TIDCP=0`): an Apple IMP-DEF access from guest EL1 produced
   `framework-abort` 39 times — *"Hypervisor.framework killed the process:
   signal 5, an internal report_fixme/unimplemented path"*, with
   `FIXME IF: "Handle this" line 82` on stderr. Nested virt in macOS 27.0
   build `26A5421a` has unimplemented paths exactly where this would live.

Recorded so the option is costed rather than forgotten, in the same spirit as
`hvf-probe/FINDINGS.md`.

## 7. Things this would have been wrong about anyway

- **SPRR would not be hardware-enforced.** Under any of these schemes the guest
  cannot enable SPRR (`SPRR_CONFIG_EL2` is UNDEF at guest EL2; at guest EL1 it
  traps to us and we would fake it), so page permissions carry standard ARM
  meaning rather than Apple's. That is fine for an emulator and **wrong for
  anything security-meaningful** — no result obtained on such a VM says
  anything about whether SPTM's protections hold on real hardware.
- **It does not serve the endgame.** On Windows ARM there are no Apple IMP-DEF
  registers in any form, so that target needs full TCG regardless. This was
  only ever a development-speed optimisation for Apple-Silicon hosts.

## 8. What is worth keeping

Both artefacts are cheap to keep and answer questions that will come up again:

- **`gxfstat`** (`target/arm/gxfstat.c`, `include/xnu/gxfstat.h`, hooks in
  `helper.c` / `translate-gxf.c` / `cputlb.c` / `scripts/darwin/dumpregs.py`).
  Costs 0.6% of boot time and gives a per-boot profile of GXF transitions,
  Apple sysreg traffic by register and by EL, MMIO, and exceptions by class.
  `DARWIN_GXFSTAT=0` silences the per-second line; the final line and the
  register histogram still print at exit.
- **`hvf-probe/hvf_exitbench.c`.** Turns "a VM exit costs about a microsecond"
  into a number for whatever Mac is in front of you, plus the native
  instruction rate and the in-guest exception cost. Ad-hoc signed, no sudo, no
  developer account.

- **`tools/time_boot.py`.** `probe.sh` says where a boot got to; this says how
  long it took, which is the denominator of every speedup claim.

## 9. Reproducing all of it

```
# device tree, if /tmp was wiped
ipsw img4 im4p extract --output /tmp/dvm/dtree_raw \
  ipsw_db/24A5430a__iPhone17,3/DeviceTree.d47ap.im4p
python3 dt_fixup.py /tmp/dvm/dtree_raw /tmp/dvm/dt_base.bin -nvram nvram.bin

# exit counts
tools/probe.sh --dtree /tmp/dvm/dt_base.bin --secs 90 --tag GXFCOUNT
grep -a '^gxfstat' /tmp/dvm/probe/GXFCOUNT.stderr.log

# per-register histogram (printed at exit)
tools/probe.sh --dtree /tmp/dvm/dt_base.bin --secs 12 --tag GXFREG
grep -a '^gxfstat reg' /tmp/dvm/probe/GXFREG.stderr.log

# guest instruction count
tools/probe.sh --dtree /tmp/dvm/dt_base.bin --secs 12 --tag GXFINSN12 -- \
  -plugin qemu-sptm/build/tests/tcg/plugins/libinsn.dylib,inline=on \
  -d plugin -D /tmp/dvm/probe/GXFINSN12.plugin.log
cat /tmp/dvm/probe/GXFINSN12.plugin.log

# exception level, from the monitor
tools/probe.sh --dtree /tmp/dvm/dt_base.bin --secs 12 --tag GXFEL2 --keep
python3 tools/hmp.py /tmp/dvm/GXFEL2.sock "info registers" | grep PSTATE

# TCG boot wall time
tools/time_boot.py --repeat 3

# HVF exit cost on this host
cd hvf-probe && make exitbench && ./hvf_exitbench 500000

# static patch-site counts in the signed firmware
python3 - <<'EOF'
import struct
for p in ["firmware/bootkc","firmware/sptm","firmware/txm"]:
    d=open(p,'rb').read(); a=struct.unpack_from("<%dI"%(len(d)//4), d, 0)
    ge=sum(1 for w in a if 0x00201420 <= w <= 0x0020143F)
    gx=sum(1 for w in a if w==0x00201400)
    sr=sum(1 for w in a if (w & 0xFFF80000) in (0xD5380000,0xD5180000)
                        and ((w>>12)&0xF) in (11,15))
    print(f"{p}: genter={ge} gexit={gx} impdef-msr/mrs={sr}")
EOF
```
