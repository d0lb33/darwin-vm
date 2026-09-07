# Apple AMX in the machine

2026-09-06. `qemu-sptm` now implements Apple's AMX (Apple Matrix
eXtensions) instruction space for the darwin machine: decode
(`target/arm/tcg/apple-amx.decode`, `translate-amx.c`), execution
(`amx_helper.c` plus the corsix/amx reference sources under `tcg/amx/`,
MIT, unchanged apart from the include line), the register file and its
migration state (`target/arm/amx.h`, `machine.c` subsection `cpu/amx`), and
the four control registers the kernel uses. It is on by default with
version 3 advertised; `DARWIN_AMX=0` removes it, `DARWIN_AMX=N` advertises
version N (1–6).

## Why

The 55-minute home-screen guest of `tcg-idle-profile.md` held 199 crash
records; the dominant ones were the same `EXC_BAD_INSTRUCTION` in four
processes, instruction word `0x00201220`: MercuryPosterExt 137×,
iconservicesagent 12×, audiomxd 6×, mediaanalysisd 5×. `0x00201000 |
op<<5 | operand` with op 17, operand 0 is AMX `set`. QEMU treated the
space as unallocated, XNU's `handle_uncategorized()` turned that into
SIGILL, and launchd kept respawning the processes. MercuryPosterExt renders
the lock-screen wallpaper, which is why the lock screen was black behind
the clock.

## The kernel's contract (24A5430a, unslid addresses)

| Register | Encoding | Use |
|---|---|---|
| `AMXIDR_EL1` | `S3_6_C15_C2_7` | `kernel+0xb359e6c` maps bits 0–5 to versions 1–6, panics on other bits; reached through the function table at `kernel+0x7ab818` |
| `AMX_CONFIG_EL1` | `S3_4_C15_C1_4` | bit 8 written as `0x100` per CPU at init (`kernel+0xc6da0c`, all versions); bit 63 ORed in at `kernel+0xc701dc` and read back at `+0xc70038`: the per-thread enable |
| `AMX_STATE_T_EL1` | `S3_4_C15_C1_3` | read at `kernel+0xc70054` (`cmp x8, #0; cset`) and 26 more sites: non-zero means live state to save |
| `AMX_CONTEXT_EL1` | `S3_4_C15_C5_0` | written with the CPU id for versions 2–3 (`kernel+0xc6da1c`); opaque |

Trap: the synchronous handler (`kernel+0xc6548c`, `lsr w25, w20, #26`)
compares the class with `0x3f` (`+0xc65624`) and the AMX branch requires
`(esr & 0x1fffff8) == 0x18` (`+0xc65f88`); it then copies the faulting
instruction (`+0xc6623c`) and either allocates the thread's save area
(`"Failed to allocate AMX state for thread"`) and sets bit 63, or panics if
the fault came from the kernel. The model raises `ESR = 0xfe000018` when an
AMX instruction runs with bit 8 or bit 63 clear.

`set` zeroes the register file and makes `AMX_STATE_T_EL1` read 1; `clr`
makes it read 0; `set` while live is UNDEF (corsix `setclr.md`).

## Loads and stores

corsix's `ldst.c` uses host pointers; `amx_helper.c` reimplements
`ld_common`/`st_common`/`ldzi`/`stzi` with `cpu_ldq_le_data_ra` and
friends, so accesses use the current mmu index (EL0 userspace and the
kernel's EL2 save/restore alike) and faults on the operand address are
ordinary data aborts at the AMX instruction's PC.

## Build note

The first build placed the 5 KiB register file, forced to 128-byte
alignment, in the middle of `CPUARMState`; the guest then hung at kernel
entry with no serial output even with `DARWIN_AMX=0` (probe `AMX0`; the
previous binary booted, `AMXCTRL`). Without the alignment attribute and at
the end of the struct the restore boot reaches the shell (`AMX2`).

## Validation

- Restore ramdisk boot with AMX on: shell reached, zero panics (`AMX2`).
- corsix's own emulator-versus-hardware test on this M5 Max host passes
  for every instruction family except GENLUT (its first iteration differs
  on this newer core; the reference targets M1–M4). The QEMU port uses the
  same compute sources unchanged.
- System-disk boot, stock kernel, PMGR, `-development-activation` tree,
  kept paused at 330 s and RAM-scanned (`SYS_AMX3`, strict `set`): 36
  crash records, of which 9 were still SIGILL on `0x00201220`
  (MercuryPosterExt 6, iconservicesagent 3): the `set`-while-live UNDEF.
- Same boot with `set` re-initialising (`SYS_AMX4`, `DARWIN_AMX_DEBUG=1`):
  **31 records, none SIGILL, none in MercuryPosterExt, iconservicesagent,
  audiomxd or mediaanalysisd** (MacinTalkAUSP 15 and KonaSynthesizer 9
  were SIGKILLs, dvm-input 5 and chronod 1 SIGABRT, all pre-existing). The
  trap log shows exactly one AMX trap in 270 s, the first EL0 `set` with
  `AMX_CONFIG_EL1 = 0x100` (thread bit clear), after which the kernel
  enabled the thread and nothing trapped again. Why `SYS_AMX3` hit the
  live-`set` case nine times while `SYS_AMX4` logged no re-initialisation
  is not explained; the lenient behaviour is kept because it removes the
  crashes and the strict one reproduced them.
- The lock screen now draws with its clock laid out correctly
  (`/tmp/dvm/SYS_AMX3/frame-0320.png`); before AMX the clock glyphs were
  sheared and overflowed the screen. The wallpaper itself is still black.
- Not yet validated: a guest-side numeric self-test of the arithmetic
  (needs an ad-hoc-signed binary in a trust cache), SMP thread migration
  of live AMX state under load, and checkpoint restore of `cpu/amx`.
