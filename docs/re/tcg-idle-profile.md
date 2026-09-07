# Where the host CPU goes while iOS idles, and what TCG can do about it

2026-09-06, worktree `tcg-idle-perf`. Guest: the durable native-SMC package
(`~/dvm-artifacts/native-smc/default.json`, iOS 27 24A5430a, 6 vCPUs,
`-accel tcg,thread=multi`, `DARWIN_PAUTH_CACHE=on`). Host: M5 Max, macOS 27,
18 cores. Unless stated, the QEMU binary is the pinned O2 build
(`54c8ee5a…`). Every number below is a fresh disk boot, not a RAM restore.
All logs live under `/tmp/dvm/idleprof/<TAG>/` and are disposable.

## Tools added

| Tool | What it measures |
|---|---|
| `tools/re/idle_host_profile.py RUN --tag T` | per-thread host CPU-seconds (`ps -M`), optional macOS `sample` call tree, vCPU PC/EL samples for all vCPUs via HMP `stop`/`info registers -a`/`cont`, serial and stderr growth |
| `tools/re/idle_host_report.py host-sample.txt` | attributes each `sample` observation once to a TCG category (translated code, TB lookup, MMU refill, PAuth, BQL/exclusive waits, softfloat, …) |
| `tools/re/thread_group_names.py SOCK threads.txt` | names the thread groups of runnable threads from a `ramscan_threads.py` listing (who is busy in the guest) |
| `tools/re/vcpu_sample_kexts.py vcpu-samples.txt` | attributes kernel PC samples to kexts through `kc_text_map.py` |

Caveat on the vCPU PC samples: an HMP `stop` lands on a translation-block
boundary, so exception vectors (`com.apple.kernel+0x400`, the lower-EL sync
entry) and the post-WFI idle return (`com.apple.kernel+0x54c8`) are
over-represented. Use them for kernel/user/idle proportions and kext
attribution, not as a cycle profile; the host `sample` is the cycle profile.

## The guest is not idle for the first ~20 minutes

`SMC_1788732102495287000` (launched 17:01:30, first presented frame at
+145 s):

| Guest state | Elapsed since launch | QEMU host CPU | Evidence |
|---|---|---|---|
| Lock screen, post-boot | 4 min | 301% | `IDLE_T4MIN`: vCPU threads 0–3 at 22.3 cpu-s each per 30 s, threads 4–5 at 0.8 |
| Lock screen | 10 min | 309% | `IDLE_T10MIN`: 60 s window, same split |
| Display off | 25 min | 83% | `IDLE_T25MIN`: 74% of vCPU samples at the idle return |
| Home screen, no input | 30 min | 231% | `HOME2_IDLE`: 19 presented frames / 25 s, 9 DCP reboots |
| Home screen, page swipes every 2.2 s | 31 min | 272% | `HOMESWIPE1_SWIPES`: 43 frames / 30 s, 13 DCP reboots, swipe ACK 20–140 ms |

Only the four efficiency-cluster vCPUs (CPU 0–3) ever run; CPU 4–5 sit at
`com.apple.kernel+0x54c8` after WFI in every sample (`darwin_smp.c` + the
Apple event-stream fix are doing their job). So "300%" means the guest's
E-cluster is saturated.

Why it is saturated: `ramscan_threads.py` + `thread_group_names.py` at 10
minutes found **200 runnable threads (TH_RUN, not TH_WAIT/TH_IDLE) in 57
thread groups**: SpringBoard 22, PerfPowerServices 15, UserEventAgent 13,
weatherd 9, mobileassetd 8, suggestd 8, navd 8, intelligenceflowd 7,
assistantd 6, and a long tail of daemons. That is ordinary post-boot
background work that a real phone finishes in a minute; at TCG speed it
takes about 20 minutes. At the home screen (`HOMECENSUS`, 30 min) the census
is 38 runnable threads in 25 groups: SpringBoard 7, backboardd 6, InputUI 2,
clipserviced 2, everything else 1.

The lock screen turns off by itself after the post-boot storm (the API
transition `FB0 Api power state transition request from ON to OFF` appears
once, at 17:07), and a swipe does not wake it; the Home button
(`relay.py --home`) does, and a second Home press unlocks to the home
screen (no passcode). `tools/input/relay.py` is the right driver for this
package: its disk carries the v6 helper (`DVM_INPUT_START version=6`), not
the DVMI2 `dvm_hid` helper, so `DARWIN_INPUT_UART=1` never reaches `R`.

## What the host is doing on a busy vCPU

`idle_host_report.py` over the 20 s `sample` of `IDLE_T10MIN` (6,761
observations per thread, waits included):

| Category | CPU 0–3 each | What it is |
|---|---:|---|
| translated code + other helpers | 34.5% | the guest's instructions |
| translated-block lookup (`helper_lookup_tb_ptr`, `qht_lookup`) | 13.4% | indirect branches and returns missing the 4096-entry jump cache; 1.45M live TBs (`info jit`) |
| MMU translation / TLB fill | 12.9% | `vmsa_ttbr_write` flushes the whole softmmu TLB on every ASID change, so every context switch refills; 4.3M partial flushes in 11 min |
| pointer-authentication helpers | 11.3% | `helper_pacib`/`autib`, `pauth_check_trap` → `arm_hcr_el2_eff`, `pauth_strip`; PAC is already disabled in this fork, this is call overhead and checks |
| host mutex wait (BQL) | 7.1% | 45% from `mttcg_cpu_thread_fn` re-taking the BQL after each `cpu_exec` exit, 10% `cpu_exec_start` (exclusive work), 9.5% `helper_get_cp_reg64` (CNTVCT reads), 8% `cpu_exec_loop` interrupts |
| host condition wait | 6.2% | `cpu_exec_start` waiting for exclusive sections (broadcast TLBI) |
| memory slow path (`probe_access`, MMIO) | 5.4% | |
| code generation | 2.4% | 9 full TB flushes in 11 min with the 1 GiB default buffer |
| TLB maintenance | 2.0% | `tlb_flush_range_by_mmuidx_async` |
| system registers / hflags | 1.6% | |
| softfloat (f32/f16, NEON) | 0.8% | rises to 6–8% while frames are being rendered (`HOME2_IDLE`, `IDLE_T4MIN`) |

Kext attribution of the vCPU samples in every display-on state:
`com.apple.kernel` 28–31%, shared-cache user code 27–32%, SPTM/TXM
(kernel-space PCs outside the kernelcache text) 5–6%, sandbox/AMFI/APFS
each ≤1.4%, and **no samples at all in IOMobileGraphicsFamily, AppleDCP,
RTBuddy or AppleFirmwareKit**. The display-on cost is SpringBoard and
backboardd compositing in software (CoreAnimation's CPU rasteriser,
`docs/re/ca-software-path.md`) at roughly 1 frame/s, plus the kernel work
that generates.

## Constant errors and crashes in the same run

- **DCP sleep/reboot loop.** With the display on, XNU asks the DCP for
  power state `0x201` every ~3 s (`asc(DCP): AP requests IOP power state
  0x201`, `AP stopped the IOP`, `dcp: coprocessor booted`): 115 cycles in
  the first 8 minutes, 181 by the swipe test. Each cycle repeats the RTKit
  HELLO, the eleven AFK endpoint starts and the IOMFB startup RPCs. The
  request is guest-initiated (kernel log `set_power_state powerState=0
  powerCallbackDcp=1`, `powerUpDART enable=0 dcp_power_off=1`), the model
  answers what `darwin_asc.c:MGMT_SET_IOP_PWR` documents, and the loop
  stops when the display API turns off. It costs almost no CPU (see the
  kext attribution) but it is the noisiest thing in both logs:
  `IOMFB_POWER_DART: set_device_power …` accounted for 33,987 of 41,195
  console lines.
- **SMC keys never published:** `AppleChargerData` polls `CHPS` and `YBk0`
  every ~18 s and logs `kSMCKeyNotFound` (36 times in 11 min);
  `AppleSmartBatteryPack` asks once for `btq0` and `BFS0`. Listed as
  unsupported in `docs/re/native-battery-smc.md`.
- **AppleSEPKeyStore** selector 142 fails 61 times and selector 31 15
  times with `e00002bc` (unsupported), from user processes.
- **Crashes.** No crash text reaches the console. `oskcdata.py` over a
  12 GiB RAM dump of the home-screen guest (`/tmp/dvm/idleprof/oskcdata-g1.txt`)
  finds **199 crash records**:

  | Process | Records | Reason |
  |---|---:|---|
  | MercuryPosterExt | 137 | `EXC_BAD_INSTRUCTION`, SIGILL, instruction `0x00201220` |
  | DumpPanic | 16 | SIGTRAP, one with `Node exceeds expected bounds` |
  | iconservicesagent | 12 | SIGILL, `0x00201220` |
  | nearbyd | 10 | SIGABRT |
  | audiomxd | 6+3 | SIGILL `0x00201220`; `writing stackshot failed, aborting` |
  | mediaanalysisd | 5 | SIGILL, `0x00201220` |
  | dvm-input, accessoryupdater | 2 each | SIGABRT / SIGTRAP |

  XNU's `handle_uncategorized()` puts the faulting instruction word in
  code[1]; `0x00201220` is Apple AMX `set` (encoding `0x00201000 | op<<5 |
  operand`, op 17, operand 0, per corsix/amx). QEMU's translator treats
  the whole `0x00201xxx` space as unallocated, so the first AMX instruction
  a process executes is a SIGILL. MercuryPosterExt is the lock-screen
  wallpaper poster; this is why the wallpaper is black. The kernel does
  know AMX: `com.apple.kernel` unslid `0xfffffff00b359e58` reads
  `AMXIDR_EL1` (`mrs x8, s3_6_c15_c2_7`), maps bits 0–5 to AMX version 1–6,
  and panics on unknown bits or no version; it is reached through the
  function table at `0xfffffff00b7af818`. The autogen Apple-register model
  backs `amxidr_el1` with zeroed opaque storage. Emulating AMX (state,
  ~22 instruction families, the lazy-enable trap the kernel expects:
  strings `AMX exception from kernel`, `Failed to allocate AMX state for
  thread`) is a real project; it has not been started.

## TCG changes in this worktree

All portable C, no guest patches, each behind a switch and off by default
until measured:

1. **O3 + LTO build** (`tools/build_qemu_fast.sh`, existing). The pinned
   package binary is O2 without LTO.
2. **`TB_JMP_CACHE_BITS` overridable** (`accel/tcg/tb-jmp-cache.h`), built
   as `build-fast-jc16` with `--extra-cflags=-DTB_JMP_CACHE_BITS=16`.
3. **Lock-free counter reads** (`ARM_CP_LOCKLESS_READ` in `cpregs.h`,
   `op_helper.c:get_cp_reg64`, applied to CNTPCT/CNTVCT/CNTVCTSS and the
   Apple `ACNTVCT`/`ACNTPCT`/`AGTCNTPCT` aliases in `helper.c`). The read
   hooks only consult the virtual clock's seqlock and per-CPU offsets;
   the BQL is still taken when icount is enabled.
4. **Inline disabled-PAC fast path** (`DARWIN_PAUTH_INLINE=1`;
   `translate-a64.c:gen_pauth2`/`gen_pauth1`, `translate.h`,
   `pauth_helper.c:arm_pauth_inline_enabled`). Only for regimes where
   `pauth_check_trap()` cannot fire (EL2+, or EL0 as `ARMMMUIdx_E20_0`);
   `pac*` becomes a register move, `aut*`/`xpac*` become the same strip the
   helper computes, guarded by a live-TCR compare that falls back to the
   helper. `tools/re/smp_smoke.py --pauth-cache verify` exercises 1,024
   randomized cases through the real instructions.

Results are recorded in the next section as they are measured.

## Measurements of the changes

First-frame time on fresh boots is too noisy to rank builds: the A/B/B/A
`TCGAB_*` runs gave O2 161.6 s and 106.5 s, O3/LTO 104.6 s and 132.0 s (a
second session's guest was running during the first), with `Early boot
complete` at 12.6/11.2 s (O2) vs 11.0/10.9 s (O3/LTO). Use a CPU-bound
marker for ranking.

Six sequential 300-second fresh boots (`TCGB_*`, `warm_boot_probe.py`,
presented frames as the work proxy, no other guest on the host):

| Run | Variant | Early boot | Frames in 300 s | DCP reboots |
|---|---|---:|---:|---:|
| 1 | O3/LTO | 9.4 s | 1,991 | 87 |
| 2 | O3/LTO + inline PAuth | 9.7 s | 1,855 | 84 |
| 3 | O3/LTO + inline PAuth, jump cache 2^16 | 54.2 s | 861 | 81 |
| 4 | O3/LTO, jump cache 2^16 | 56.9 s | 957 | 79 |
| 5 | O3/LTO + inline PAuth | 8.2 s | 1,589 | 74 |
| 6 | O3/LTO | 11.1 s | 1,602 | 79 |

**The 2^16 jump cache is a clear loss**: `tcg_flush_jmp_cache()` runs on
every TLB flush (about 6,500/s in this guest, see `info jit` above), and a
1 MiB per-vCPU cache turns each into a 1 MiB memset. The override stays at
the upstream 4096 entries. Frames-per-300-s is too dependent on display
timing to separate O3/LTO from O3/LTO + inline PAuth (both pairs overlap);
the CPU-bound ranking below is the measurement that counts for them.

### CPU-bound ranking

`tools/time_boot.py --dtree firmware/dtree --repeat 3` boots the restore
ramdisk on one vCPU to `/bin/sh: can't access tty` (327 serial lines, no
display, no other guest on the host). Two rounds, medians of three:

| Binary | Round 1 | Round 2 | vs pinned O2 |
|---|---:|---:|---:|
| `qemu-sptm/build` (O2, no LTO; what `~/dvm-artifacts/native-smc` pins) | 4.35 s | 4.43 s | — |
| `build-fast` O3/LTO + lock-free counter reads, `DARWIN_PAUTH_INLINE=0` | 3.90 s | 3.96 s | −10% |
| `build-fast`, `DARWIN_PAUTH_INLINE=1` | 3.49 s | 3.46 s | −20% |
| `build-fast-jc16`, inline on | 4.12 s | 4.10 s | worse than the row above by 18% |

Run-to-run spread within a round is ≤0.1 s (`/tmp/dvm/idleprof/bench2.log`).
The inline path is therefore on by default in this fork
(`pauth_helper.c:arm_pauth_inline_enabled`); `DARWIN_PAUTH_INLINE=0` keeps
the helper-only path for A/B runs. The lock-free counter read and O3/LTO
are not separated by this table; the earlier `arm-tcg-performance.md`
measured O3/LTO alone at 14% on a different workload.

Correctness evidence for the inline path: `tools/re/smp_smoke.py
--pauth-cache on` and `--pauth-cache verify` (1,024 randomized TCR /
address-half / key cases through the real `aut*`, `xpac*` and signing
instructions, checksum `0x5000000000000000`) with `DARWIN_PAUTH_INLINE=1`
on both builds, plus the 300-second iOS boots `TCGB_2` and `TCGB_5` (zero
panics, normal frame counts). The final default-on binary
(`build-fast`, 18:46) was validated again (`final_validate.log`): all four
smoke modes pass (`--pauth-cache on`/`verify`, plain, `--wfe`), restore
boot 3.45 s default vs 3.93 s with `DARWIN_PAUTH_INLINE=0`, and a 240 s
iOS boot `TCGFINAL_1_fastdef` reached `Early boot complete` at 8.0 s with
1,820 presented frames, 70 DCP reboots and zero panics.

**Recommendation:** repackage `~/dvm-artifacts/native-smc` with the
`build-fast` binary (`tools/rootfs/package_native_smc.py` records the new
hash); the packaged O2 binary leaves 20% on the table on the CPU-bound
path.

## Next steps, in the order the user chose (TCG first, then DCP)

1. **Remaining TCG cost centres**, measured, not yet attacked: MMU refill
   after ASID-change flushes (13%), TB lookup (13%), the BQL re-take on
   every `cpu_exec` exit and the exclusive sections for broadcast TLBI
   (~13% together). The first two need an ASID-tagged softmmu TLB or a
   cheaper jump-cache invalidation, both upstream-sized changes; the third
   is a QEMU core change (`cpus.c`/`cputlb.c`).
2. **DCP sleep/reboot loop: still open, three hypotheses eliminated.**
   PMGR is now modelled (`native-pmgr.md`) and `use_psd_dcp_power2` stays
   0 because the H17P pipeline's platform traits hard-code it for
   iPhone17,3 (a bare `mov w0, #0` at `AppleMobileDispH17P-DCP+0x3288`,
   captured live). Apple's own `iomfb_IdleDetectorIndex_PowerGateFrontEnd_enabled=0`
   boot-arg changes nothing (`SYS_NOGATE1`: 114 reboots / 300 s), and
   answering the AP's `A385` poll with 1 changes nothing either
   (`SYS_A385_1`: 110 reboots, 47,832 polls). Per cycle the AP polls
   `A385` six or seven times, sends `A500` (4 bytes in), puts the eleven
   AFK endpoints into state 2 and requests IOP power `0x201`; the kernel's
   `set_power_state(0)` log carries `powerCallbackDcp=1`. The remaining
   lead is the meaning of `A500`/`A385` and what a real DCP answers there.
3. **AMX.** Every SIGILL crash above is the same missing instruction set.
   Options: emulate AMX (state, ~22 instruction families per corsix/amx,
   plus the lazy-enable trap XNU expects), or first find how userspace
   learns AMX presence (the shared cache logs `AMXVersion=%d` and
   `non-AMX/SME iOS device`) and make the model report the truth about
   itself. Not started; needs the user's go-ahead.
