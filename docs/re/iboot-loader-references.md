# What an iBoot path must replace in the current direct loader

**Scope.** This note is a static, build-specific inventory for the currently
pinned `qemu-sptm` revision `607e977e26c31ac9b5be417c6bcc38dc88f3b3ad` and
the darwin-vm tree at `17fdc519eca9de8f77c1dd5b55f9c60d575a5193`.  That QEMU
commit is present in the local submodule object store but is no longer
fetchable from its configured remote; all QEMU references below are to
`git show 607e977:<path>`, not to the active checkout (which has unrelated
dirty files).

This is deliberately an inventory of the **synthetic contract the direct
loader currently provides**, not a claim that those values are the real iBoot
implementation.  Apple does not publish iBoot headers: the public XNU
`pexpert/pexpert/arm64/boot.h` explicitly says it must duplicate
`IBOOT_MAX_ENV_VAR_DATA_SIZE`.  XNU's public sources nevertheless establish
the XNU-facing `boot_args` ABI and kernel entry usage:

- [Apple XNU `boot.h`](https://github.com/apple-oss-distributions/xnu/blob/main/pexpert/pexpert/arm64/boot.h)
  defines revision/version 2 and the `boot_args` fields (lines 493-527 as
  retrieved 2026-09-04).
- [Apple XNU `start.s`](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/arm64/start.s)
  states the cold-kernel entry receives boot args in `x0` (lines 2716-2737),
  moves that value to `x20`, and later reads `virtBase`, `physBase`, `memSize`,
  and `bootFlags` through `x20` (lines 2477-2490, 2995-3018).
- [Apple XNU `arm_init.c`](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/arm/arm_init.c)
  says iBoot populates EDT properties for XNU (lines 2528-2536).  It does not
  document iBoot's reset ABI, IMG4 flow, or MMIO programming.

The public `boot.h` currently has `BOOT_LINE_LENGTH == 608`, while the pinned
local shim uses 1024 (`qemu-sptm/include/xnu/boot/args.h:16,48-62`).  Therefore
the loader must use the header/ABI matched to the selected kernelcache, not the
latest public header as a drop-in replacement.

## Current path in one sentence

`run.sh`/`probe.sh` pass already-unwrapped images to `-M darwin`; QEMU maps and
patches them, fabricates the EDT memory map and `boot_args`, then resets the
CPU straight into SPTM with the physical BootArgs pointer in `x0`.

The call chain and selected files are explicit:

- `run.sh:78-94` and `tools/probe.sh:142-162` wire `-bootkc`, `-dtree`, `-tc`,
  `-ramdisk`, `-args`, `-m`, and conditionally `-sptm`/`-txm`.
- `qemu-sptm/hw/arm/darwin.c:250-291` opens those inputs, determines DRAM from
  `/chosen`, and calls `arm_load_xnu()` before reset.
- `qemu-sptm/hw/arm/xnuboot_sptm.c:634-636` selects the SPTM path merely when
  `info->sptm` is non-NULL; it does not select or execute iBoot.
- `qemu-sptm/hw/arm/darwin.c:78-94` resets the CPU then writes
  `init_x0`/`init_pc`, which the SPTM loader supplies at
  `xnuboot_sptm.c:526-527`.

## Replacement inventory

| Contract presently synthesized | Direct-path evidence | What an iBoot path must provide or prove |
|---|---|---|
| **CPU reset / exception level** | The machine creates a CPU with EL2 enabled and EL3 disabled (`darwin.c:266-280`), asserts `arm_highest_el(env) == 2`, resets it, sets only `x0` and `pc`, and enables FP in CPACR_EL1/CPTR_EL2 (`darwin.c:78-94`). | Do not reuse this as iBoot's ABI. It is the current QEMU-to-**SPTM** handoff. Establish reset vector, EL, PSTATE, system-register state, and live input registers by tracing the exact iBoot build. If iBoot is entered earlier than SPTM, it will also require the pre-SPTM hardware state that the current machine skips. |
| **DRAM and load allocator** | DRAM is allocated at `/chosen/dram-base,size` (`darwin.c:286-292`, `xnuboot_sptm.c:39-45`).  The loader constructs a contiguous blob at `dram_base`, with 16 KiB rounding (`xnuboot_sptm.c:176-186,204-207`), and depends on a deliberately calculated initial hole (`:209-249`). | Either let observed iBoot choose all destinations, or reproduce the exact destinations from a trace.  An iBoot loader must not pre-place data in the ranges that iBoot will treat as free.  The current packing/order is a compatibility implementation, not an authoritative iBoot layout. |
| **Mach-O interpretation and placement** | `macho_get_info()` derives low/high virtual addresses and an `LC_UNIXTHREAD`/`LC_MAIN` entry (`macho.c:43-80`); `macho_load()` copies every `LC_SEGMENT_64` to `phys_base + vmaddr - virtlo` and zero-fills BSS (`macho.c:82-115`).  The SPTM loader pushes selected segments in a hard-coded order (`xnuboot_sptm.c:257-389`) and loads the whole SPTM Mach-O at the blob head (`:323-324`). | Preserve image formats and verification until iBoot consumes them.  Replacing this with a generic raw `-kernel` mapping is not equivalent: this path uses Mach-O segments, not an ELF/Image header.  Record the actual `load` addresses before removing this loader. |
| **SPTM / TXM / BootKC virtual contract** | The current code assumes TXM and BootKC are respectively 1 and 2 × `0x10000000` from SPTM (`xnuboot_sptm.c:28-31,437-447`), publishes `*-virt` and `*-entry` properties (`:430-435`), and maps every TXM/SPTM/BootKC region in `/chosen/memory-map` (`:257-389`).  `apple_regs_init()` derives CTRR/CTXR limits from those map entries (`apple_regs.c:114-143`). | This is the highest-risk replacement.  iBoot execution must either produce the same monitor/EDT contract or the machine must observe and honor its different contract.  Do not retain only `BootKC-entry`: SPTM/TXM protection and Apple register setup use the individual ranges. |
| **BootArgs** | The pinned shim defines revision/version 2 and all fields at `include/xnu/boot/args.h:41-62`.  It writes revision, virtual/physical bases, MTE-adjusted `memSize`, `memSizeActual`, `topOfKernelData`, EDT pointer/length, command line, and video (`xnuboot_sptm.c:449-470`), then writes the struct to guest RAM (`:484-490`). | iBoot must build a kernel-compatible structure and pass it on its own final kernel entry.  `x0=BootArgs` is the public XNU cold-kernel convention, but it is **not** evidence that direct entry to iBoot uses `x0` that way.  Retain an assertion/dump that records the final struct, including `memSizeActual` for MTE. |
| **Device tree and memory-map** | `dt_fixup.py` replaces firmware/version strings, DRAM values, CPU state/frequencies, deterministic random seed, CTRR flag, serial and NVRAM settings (`dt_fixup.py:594-620`).  It initializes all SPTM map slots to `(-1,-1)` and sets slide zero (`:538-561`); QEMU overwrites the populated map slots (`xnuboot_sptm.c:117-129,257-435`). | Start from an unmodified, matching board EDT and diff iBoot's resulting tree against it.  Retire a fixup only when the iBoot trace proves it writes that property.  Never expect iBoot to accept this project-specific `firmware-version=qemu-sptm`: `darwin.c:232-243` currently rejects any other value. |
| **iBoot-added per-device EDT state** | Kept DART nodes receive invented unique `dart-id`s because SPTM otherwise reports `error -1 getting dart-id` (`dt_fixup.py:176-184`; corroborating boot evidence `docs/re/dcp-bringup.md:33-39`).  RTBuddy nubs receive `pre-loaded`, synthetic regions, and `no-firmware-service` because no iBoot IOP image loader exists (`dt_fixup.py:293-356`).  SEP gets `rom-panic-bytes`, `sepfw-loaded`, a synthetic `SEPFW` map entry, `sepfw-load-at-boot=0`, and a nonzero chip ID (`:358-475`). | These are independently testable iBoot responsibilities.  Preserve each as an explicit compatibility overlay until observed iBoot produces an equivalent final EDT and bytes.  In particular, do not let iBoot's IOP/SEP loading silently use the zero-filled emulation placeholders. |
| **Framebuffer carveout** | The direct path takes space from top-of-DRAM, reduces `memSize`, fills `boot_args.Video`, writes `/chosen/memory-map/PurpleGfxMem`, and updates `/vram/reg` (`xnuboot_sptm.c:54-106`). | Either support iBoot's actual framebuffer allocation/EDT output or retain this as a post-iBoot QEMU display adapter only after proving it does not conflict with iBoot's reservation.  It is not merely a UI setting: it changes XNU-managed RAM. |
| **Trust caches** | `trust_cache_offsets_t` is documented locally as “the structure iBoot uses” (`include/xnu/boot/trustcache.h:3-13).  The direct loader creates a one-cache, offset-8 header (`xnuboot_sptm.c:131-137`) and writes it plus one raw module (`:500-516`). | iBoot's XNU interface is a list, not a single-file QEMU option.  Keep one or more raw modules and their offsets intact.  The current host merge workaround is documented in `tools/rootfs/merge_tc.py:1-8` and `docs/re/rootfs-assembly.md:268-306`; it is a workaround, not iBoot behavior. |
| **Ticket / IMG4 / personalization** | QEMU actively rejects IM4P containers (`darwin.c:96-123`; `macho.c:10-20`) and only checks Mach-O magic/CPU/file type (`macho.c:22-41`).  The download helper extracts IM4P payloads before QEMU receives them (`get_files.sh:85-100,115-129`).  `xnu_boot_info` and the machine properties contain no ticket, IM4M, manifest, or root-hash input (`include/xnu/boot/xnuboot.h:42-61`; `darwin.c:380-388`). | A real iBoot experiment needs the matching IMG4 containers and whatever ticket/manifest/personalization inputs its exact build consumes.  There is no local authoritative iBoot parser or ticket ABI, so no fabricated `-ticket` interface is justified.  Keep the present direct path's security posture explicit: it bypasses image verification rather than emulating it. |
| **Root hash / boot-manifest handoff** | The ANS path logs that it cannot retrieve `system-volume-auth-blob` from the DT because iBoot did not forward a root hash (`docs/re/ans-nvme-references.md:895-903`).  `docs/re/rootfs-boot.md:220-243` identifies the bootkc string `import_iboot_forwarded_roothash`, but correctly labels the policy inference. | Treat root-hash forwarding as a separate deliverable from IMG4 ticket acceptance.  Capture the final `/chosen` property name, type, and bytes from a real iBoot boot before adding a loader field. |
| **Kernel mutation** | Before loading, the direct SPTM path calls `patch_kc()` (`xnuboot_sptm.c:179-182`).  At least one patch rewrites the AppleImage4 `ignition_blob` handler in the host-mapped kernelcache (`xnu_patch.c:33-92`). | Do not combine “execute stock iBoot” with host-mutating its selected kernelcache and call that a verified chain.  Make mutation opt-in and record whether iBoot verified the pre- or post-patch bytes; otherwise attribution of a verification failure is impossible. |
| **Register handoff to XNU** | The direct path's terminal state is `pc = physical SPTM entry`, `x0 = physical BootArgs` (`xnuboot_sptm.c:526-527`, `darwin.c:84-93`).  Public XNU's `start_first_cpu` moves its own `x0` BootArgs to `x20` (`start.s` lines 2716-2737). | There are two handoffs: QEMU→SPTM now, and monitor/iBoot→XNU later.  Instrument both, and do not conflate them.  The exact SPTM→XNU register state is a gap in this repository's source evidence. |
| **Apple implementation registers / MTE** | QEMU seeds Apple-specific register values then uses SPTM map entries to seed CTRR/CTXR and tag offset (`apple_regs.c:90-155`).  MTE allocates tag memory at DRAM/32 (`darwin.c:204-219`) and forces `memSize=31/32` in BootArgs (`xnuboot_sptm.c:459-465`). | iBoot may set or depend on some of this state, but no local iBoot disassembly proves which.  Keep all values as existing machine compatibility state, log them on entry, and only remove/relocate after an instruction trace shows iBoot overwrites or does not use them. |
| **MMIO and pre-kernel hardware state** | Device models and the low-priority `/arm-io` catch-all are installed before reset (`darwin.c:301-357`).  The catch-all converts otherwise-fatal MMIO faults into zero/last-write behavior (`darwin_unimp.c:1-9,59-85,138-158`). | This is a runtime XNU survival device, not evidence that iBoot needs no MMIO.  Running iBoot can hit PMGR, security, storage, UART, clock, or ROM registers before existing drivers touch them.  Start with logging catch-all accesses; promote only addresses with observed, branch-relevant semantics. |
| **Command-line wiring** | The only public boot inputs are `bootkc,args,dtree,sptm,txm,tc,ramdisk,fb,fbmode` (`darwin.c:56-64,380-388`).  The normal launcher hard-codes direct inputs and a boot-arg string (`run.sh:76-94`); probe does the same (`tools/probe.sh:142-162`). | Add a distinct, mutually exclusive `-iboot` mode rather than overloading `-bootkc` or silently changing direct mode.  It needs explicit container/personalization inputs only after their ABI is observed.  Preserve the old options and command line verbatim for regression comparison. |

## Contradictions and material gaps

1. **No iBoot binary or entry analysis is present in this checkout.** The project
   has direct-loader and XNU/SPTM evidence, but no authoritative local iBoot
   reset-vector disassembly.  The direct `x0` setup is consequently not an
   iBoot ABI claim.
2. **The required pinned QEMU source is locally readable but the gitlink cannot
   be freshly populated.** `git submodule update --init` fails because the
   remote lacks `607e977…`; do not make reproducibility depend on a network
   fetch of that revision.
3. **`boot_args` is version-sensitive.** The local `BOOT_LINE_LENGTH=1024`
   conflicts with public mainline's 608.  The struct's field order is stable in
   the cited sources, but byte size must be checked against the target KC.
4. **Real-image verification is absent by construction.** The paths remove
   IM4P before launch and have no ticket property.  A loader cannot truthfully
   claim secure-chain behavior until it has an observed IMG4/ticket exchange.
5. **Several DT values intentionally describe emulation, not hardware.**
   `qemu-sptm` firmware strings, deterministic seed, disabled CTRR flag,
   synthetic IOP regions, and zero-filled SEP image are compatibility choices.
   Letting iBoot overwrite them may be correct, but must be measured per
   property.
6. **No source evidence specifies iBoot's MMIO requirements.** The catch-all
   makes a first run diagnosable, but zero reads/remembered writes must not be
   represented as device semantics.

## Minimal implementation recommendation

An implementation is justified only as an **instrumented experimental
execution mode**, not as a replacement for the proven direct boot path:

1. Add a mutually exclusive `-iboot <container-or-raw-image>` machine property
   and retain every existing direct input/mode unchanged.  Do not introduce
   ticket/IM4M flags until the specific iBoot build demonstrates their
   transport.
2. In this mode, map only the observed iBoot image/reset vector and preserve
   the existing CPU/Apple-register/device setup initially.  Log PC, PSTATE,
   current EL, `x0`-`x3`, and every catch-all MMIO access before the first
   SPTM/BootKC transition.  The first success criterion is an evidenced
   handoff, not a userspace boot.
3. Snapshot and compare `/chosen`, `/chosen/memory-map`, `boot_args`, and the
   SPTM/TXM/BootKC physical ranges immediately before the final kernel entry.
   Use that delta to retire individual direct-loader responsibilities.
4. Keep `arm_load_xnu_sptm()` available as the control.  Do not pre-load or
   patch SPTM/TXM/BootKC in iBoot mode unless the trace proves the selected
   iBoot expects those already resident; doing so would defeat the question
   being tested.
5. Stop and classify the first missing MMIO or verification dependency.  Only
   then add a narrow model/loader input backed by the recorded access.  A broad
   “iBoot loader” that fabricates the current map, BootArgs, ticket success, and
   IOP/SEP state would simply reimplement the direct loader under a new name.

This recommendation does **not** justify an immediate boot experiment: this
worktree intentionally has no firmware/disk artifacts, and the actual iBoot
payload, reset ABI, and image-authentication inputs remain unproven.
