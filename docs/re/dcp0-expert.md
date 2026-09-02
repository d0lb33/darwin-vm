# dcp0-expert register map

Source: iOS 27.0 beta (24A5430a), iPhone17,3 (iPhone 16, t8140/H17P),
kernelcache `firmware/bootkc`, kext `com.apple.driver.AppleDCP` extracted to
`/tmp/dvm/kexts/com.apple.driver.AppleDCP`, device tree `firmware/dtree` /
unpatched `/tmp/dvm/dtree_raw`. Extracted and disassembled 2026-09-01.

## Summary

`AppleDCPExpert` (`IONameMatch "dcp-expert-v1"`, `IOProviderClass
AppleARMIODevice`, in `com.apple.driver.AppleDCP`) binds to `/arm-io/dcp0-expert`
and drives it purely through device-tree-selected register *indices*, not
fixed offsets: properties named `*-reg-index` pick which `reg[]` entry the
driver maps with `mapDeviceMemoryWithIndex()`. On this device only one such
index is populated (`dcp-controls-reg-index = 1`), and the code that consumes
it only ever **writes** that register — it is never read back. Two other
`*-reg-index`-style properties named in the task (`security-reg-index`,
`gapf-reg-first-index`/`gapf-count`) are present in the device tree but their
literal property-name strings do not exist anywhere in the 77 MB kernelcache,
meaning no compiled XNU driver looks them up by name in this build. A QEMU
model of `dcp0-expert` needs to accept the one write and can leave every
register a logged no-op returning zero.

## Register map

| reg | Address (node-local) | Guest phys (this VM) | Len | DT property that selects it | Value(s) | Access observed | Evidence |
|---|---|---|---|---|---|---|---|
| 0 | 0xf82b8044 | 0x3082b8044 | 0xc | none (hardcoded index `0`) | — | mapped + vaddr cached; likely interrupt config/status for `interrupts=0x2b5`; **no confirmed read** | `mapDeviceMemoryWithIndex(0,0)` at AppleDCP `0xfffffff0089ee860`-`0xfffffff0089ee86c` (`mov w1,0; mov w2,0; blraa` vtable+0x3d0); vaddr cached to `+0x98` and handed to an interrupt-registration thunk `fcn.fffffff0089f6930` (AppleDCP `0xfffffff0089f5074`/`0xfffffff0089f5080`), which is a GOT-imported call (no read of the mapped memory found in either function) |
| 1 | 0xf8008000 | 0x308008000 | 0x4 | `dcp-controls-reg-index = 1` | on=`0x10`, off=`0x0` | **write-only, confirmed** | Property values read at AppleDCP `0xfffffff0089f0564`/`0x7bf`/`0x802` (strings `dcp-controls-reg-index`, `dcp-controls-value-on`, `dcp-controls-value-off`); mapped via `mapDeviceMemoryWithIndex(1,0)` at `0xfffffff0089f05bc`-`0x89f05d0`; the actual write happens in `AppleDCPExpert::_changeDCPPowerStateInternal` (`fcn.fffffff0089f3c4c`, string xref `0xfffffff0073aeb29`): `ldr x8,[x21,0x1f0]` (cached vaddr) then `str w9,[x8]` at `0xfffffff0089f3db8` (off-value) and `0xfffffff0089f3dec` (on-value) — no `ldr` from that vaddr anywhere in the function |
| 2 | 0x2023ac050 | 0x4123ac050 | 0x4 | `security-reg-index = 2` | — | **never referenced** | String `security-reg-index` has zero hits in `firmware/bootkc` (`grep -a -c "curity-reg" firmware/bootkc` → 0); exhaustive search of every `mapDeviceMemoryWithIndex`-style call site in the kext (byte patterns `08420f91` and `117a80d2`, the two register-allocations r2 emitted for the `add x,x16,0x3d0` vtable dispatch) finds only 4 call sites total (indices for `pmgr-scratch-index`, `pmgr-soc-pwrgate-index`, `dcp-controls-reg-index`, `axi2af-reg-index`, plus the one hardcoded index `0`) — none is `2` |
| 3–16 | 0x202370000 … 0x202390000 (14 × 0x40) | 0x412370000 … 0x412390000 | 0x40 each | `gapf-reg-first-index = 3`, `gapf-count = 0xe` | — | **never referenced by name; real fault-decode code exists but is interrupt-gated** | Strings `gapf-reg-first-index`/`gapf-count` have zero hits anywhere in `firmware/bootkc`. `AppleDCPExpert` *does* contain a GAPF fault handler (`fcn.fffffff0089f45fc`) with three format strings — `"[AppleDCPExpert:0x%llx] GAPF error with empty assertion mask\n"` (`0xfffffff0073af144`), `"GAPF Error (0x%x): Master ID %u, Address 0x%llx, CMD %s\n"` (`0xfffffff0073af18c`), `"GAPF Error (0x%x): failed to find violated GAPF\n"` (`0xfffffff0073af1eb`) — that iterates an `OSArray` cached at instance offsets `+0x4c0`/`+0x4c8` and logs Master ID/Address/CMD for whichever entry has bit 0 of a status word set. Could not trace, within budget, what populates `+0x4c0`/`+0x4c8`, so cannot confirm it derives from `reg[3..16]`. This handler is reached only from an actual GAPF-violation interrupt condition, not from `start()` |

Guest-physical addresses are `node-local address + 0x210000000`. The offset is
the parent base of the first `/arm-io` `ranges` triplet in this device's
device tree (`child=0x0 parent=0x210000000 size=0x2f0000000`, read from
`/tmp/dvm/dtree_raw` — this is what `qemu-sptm/hw/arm/darwin.c`'s
`arm_io_ranges[IO_RANGE_BASE_OFFSET]` resolves to for this device tree, since
every `dcp0-expert` `reg[]` entry falls inside that first, largest range).
Checked against the task's own numbers: `0xf8008000 + 0x210000000 =
0x308008000`, matching reg[1]'s stated guest address exactly.

## `*-reg-index` properties actually present on this device

Read from `firmware/dtree` / `/tmp/dvm/dtree_raw` with the repo's ADT decoder
(`dt_fixup.py`'s `decode_node`), node `/arm-io/dcp0-expert`:

```
dcp-controls-reg-index = u32:0x1      security-reg-index    = u32:0x2
dcp-controls-value-on  = u32:0x10     gapf-reg-first-index  = u32:0x3
dcp-controls-value-off = u32:0x0      gapf-count            = u32:0xe
remote-power-state     = u32:0x1      require-force-wakeup  = u32:0x1
join-power-plane       = u32:0x1
```

`AppleDCPExpert::start()`'s config-parsing helper (`fcn.fffffff0089efb2c`,
99 KB of disassembly saved during this investigation) reads a much larger,
generic property table shared across Apple's various coprocessor-expert
device trees — in address order: `disable-power-manage`,
`support-standalone`, `support-dynamic-sysfe-pg`, `join-power-plane`,
`support-psd-self-vote`, `require-pmgr-scratch`, `pmgr-scratch-index`,
`pmgr-scratch-value-on/off`, `require-conditional-retention`,
`pmgr-soc-pwrgate-index`, `conditional-retention-size/offset/enable/disable`,
`function-enable_autopm`, `require-force-wakeup`, `dcp-controls-reg-index`,
`dcp-controls-value-on/off`, `function-force_wake`, `require-axi2af-reset`,
`axi2af-reg-index`, `axi2af-tlimit-offset`, `remote-power-state`. Every
`require-*` boolean gates a corresponding `*-reg-index` memory-map call
(`pmgr-scratch`, `pmgr-soc-pwrgate`, `axi2af`); on this device's node **none**
of those `require-*` properties are present, so `getProperty()` returns NULL,
the boolean reads as false, and `mapDeviceMemoryWithIndex()` for those three
never executes. **The only reg-index that actually gets mapped for this device
is `dcp-controls-reg-index = 1`.** `security-reg-index`,
`gapf-reg-first-index`, and `gapf-count` are not part of this generic table at
all — no code path in the function reads them.

`require-force-wakeup = 1` and `remote-power-state = 1` are consumed too, but
neither drives a `reg[]` index: force-wakeup goes through the RTKit mailbox
(`fcn.fffffff0089f2620` sends a message via a vtable call on a cached endpoint
object at `+0x210`, not raw MMIO), and `remote-power-state`/`join-power-plane`
feed IOPM/RTKit power-plane bookkeeping. Neither is relevant to the
`dcp0-expert` register map.

## Must return a real value vs. write-only / ignorable

| reg | Verdict | QEMU recommendation |
|---|---|---|
| 0 | Ignorable (write-side only confirmed) | Keep as logged no-op / accept writes silently. No evidence of a read anywhere in the traced code; if force-wake or interrupt work later shows a read-and-branch here, revisit. |
| 1 | **Write-only — no readback required** | Keep the current no-op write-accept behavior (already the case per `docs/re/dcp-bringup.md`). QEMU never needs to synthesize a value for this register because `AppleDCPExpert` never reads it back after writing `0x10`/`0x0`. |
| 2 | Ignorable | Property name never consumed; leave as unmodelled no-op returning 0. |
| 3–16 | Ignorable for boot; open item for interrupt-injection work | Leave as unmodelled no-op returning 0. Only matters if/when this VM starts injecting a real GAPF-violation interrupt (`0x2b5`) into the guest — not needed for reaching a shell. |

None of `dcp0-expert`'s 17 registers were found to be **read and branched on**
during `AppleDCPExpert::start()` for this specific device tree. This is
consistent with the empirical result already recorded in
`docs/re/dcp-bringup.md`: after `darwin_unimp.c` started backing unmodelled
`/arm-io` ranges with logged zero-reads, the DCP-enabled tree boots to a shell
with no panic — i.e., nothing downstream of `dcp0-expert` depends on a
specific readback value from any of these registers during the boot path
exercised so far.

## Open questions

- What populates the `OSArray` at `AppleDCPExpert`'s instance offsets `+0x4c0`
  (count) / `+0x4c8` (array) that `fcn.fffffff0089f45fc`'s GAPF fault handler
  iterates. Could not trace this within the investigation budget. If it does
  turn out to be built from `reg[3..16]`, it's still gated behind an actual
  hardware interrupt condition (a real AXI/GAPF protection-fault IRQ), not
  exercised by normal `start()`/boot.
- Why `security-reg-index`/`gapf-reg-first-index`/`gapf-count` exist in the
  device tree at all if no kernelcache code reads them by name. The index
  arithmetic is suggestive: `gapf-reg-first-index (3) + gapf-count (14) = 17`,
  exactly the node's total `reg[]` count, and `security-reg-index = 2` sits
  right before it — consistent with these being consumed by iBoot/SecureROM
  (or SPTM/TXM) to program the SoC's AXI address-protection firewall before
  XNU boots, rather than by anything in `com.apple.driver.AppleDCP`. Not
  verified — no iBoot/SPTM binary was examined for this report.
- Exact IOKit method names behind the PAC-signed vtable calls (`blraa`) used
  throughout — this kext ships with no exported symbol table, so all
  "mapDeviceMemoryWithIndex", "getVirtualAddress", "getObject", etc. names in
  this report are inferred from call-site *pattern* (arguments, ordering
  relative to a property read, and known IOKit conventions), not from a
  symbol. Confidence is high for `mapDeviceMemoryWithIndex(index, options)`
  given 4 consistent, independently-triggered call sites; lower for the
  smaller helpers around reg[0] and the GAPF array.
- reg[0]'s actual hardware purpose (12 bytes = 3 registers at `0xf82b8044`,
  outside the `0x2023xxxxx` DCP-local block the rest of the node lives in,
  closer to a typical AIC/interrupt-config range) was not independently
  confirmed against any Linux or public source — only inferred from the
  call-site pattern (mapped unconditionally, vaddr handed straight to an
  interrupt-registration thunk).
