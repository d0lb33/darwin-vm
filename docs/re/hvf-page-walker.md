# Explicit descriptor I/O for the ARM walker — 2026-09-05

This is the first QEMU integration component for the virtual-EL compatibility
backend. It does not install native shadow mappings or advance SPTM yet.
Subsequent work in `hvf-sptm-shadow-handoff.md` connects this walker to
temporary native mappings and reads using real captured SPTM tables.
Worktree: `/Users/jdolbe1/Downloads/darwin-vm-arm-native-experiments`, parent
and submodule branch `codex/arm-native-experiments`. All native work remains
uncommitted, including the new files in the submodule.

## Implemented

`target/arm/internals.h` now declares `ARMPTWMemoryOps` and
`get_phys_addr_with_ops()`. `target/arm/ptw.c` uses the existing architectural
walker with explicit physical descriptor read/compare-exchange callbacks.

The existing normal walk depends on TCG's `probe_access_full_mmu()` when
translating descriptor addresses. The debugger path avoids that dependency
but suppresses access-flag faults and architectural updates, so it cannot
serve as a permission-enforcing shadow walker. The new path recursively
translates descriptors without the soft TLB while retaining normal checks,
AF/dirty updates, access type, and fault information. Recursive walks inherit
the callbacks. A descriptor update checks stage-2 write permission before
calling compare-exchange when the original table access was read-only.

Callbacks receive physical address, security attributes, width and byte order.
They can collect source-table dependencies and coordinate writes. They return
decoded descriptor integers; compare-exchange returns the value observed
before the exchange. I/O errors become external-on-walk faults. No Apple host
API is used by this interface: it can serve another native ARM backend too.
The existing TCG entry point and its memory-access path remain in place.

## Verified under HVF and compared with actual TCG accesses

`tools/perf/native_ptw_qemu.S/.py` drives an opt-in diskless test entry in
`target/arm/hvf/ptw-probe.h`, selected only by `QEMU_HVF_PTW_PROBE=1` and
SMC immediate `0xd300`. It requires one MMU-off EL1 vCPU with nested EL2 off.
The supplied translation configuration is temporary software state: the
fixture's hardware MMU remains off. After the walk, the original CPU state
is restored and the result is returned to guest RAM. Callbacks accept RAM
only and perform atomic descriptor updates with dirty tracking.

Each case starts with fresh RAM. Two distinct TTBR trees test low and high
addresses; only the selected tree maps the target. The matrix covers EL1
and EL0 reads, writes, instruction fetch, RO/NX rejection, privileged-page
rejection, invalid leaves, AF-clear faults, HA/HD updates, and injected
descriptor read/update failures. It checks the fault code, physical address,
requested permission, number of descriptor accesses/updates, final descriptor,
and unchanged data after denied operations.

- `/tmp/dvm/HVF_PTW_DIRECT2/results.json`: 48/48 cases pass.
- `/tmp/dvm/HVF_PTW_HOST_CONTROL2/results.json`: 48/48 host-feature controls pass.
- `/tmp/dvm/HVF_PTW_TCG2/results.json`: 40/40 comparable real-access cases pass.

The TCG control executes actual LDR, STR, or a call into the target page
using the same table construction. Its exception handler records ESR/FAR/ELR;
successful stores change the target marker. Injected callback I/O failures
have no corresponding TCG device fixture and are excluded from those 40 cases.

The tested HVF CPU model has HAFDBS=0. To test *software* access/dirty updates,
the positive probe temporarily selects HAFDBS=2 for the software walker only;
it never writes that feature value to HVF or advertises it to the native guest.
The control keeps HAFDBS=0 and verifies the expected AF/permission faults with
no update. This is not evidence that native HVF supports HA/HD. The first
fixture mistakenly expected HA/HD with the host feature set and failed those
cases correctly. The TCG handler originally faulted on its own user-accessible
results buffer because exception entry set PAN; the final fixture explicitly
uses SCTLR.SPAN=1. No iOS permission check was changed.

Verified executable SHA-256:
`206891b39886bee45fa85c04ec231b88509667165cbc0e5a36c82e3e43f65699`.
Build log: `/tmp/dvm/HVF_PTW_BUILD2.log`. Later source edits are comments only.
Host regressions: 23/23, `/tmp/dvm/HVF_PTW_HOST1.log`.

The rebuilt executable also passes the three MMU-on HCR/RO/NX tests in
`/tmp/dvm/HVF_PTW_HCR_REGRESSION1/results.json`. Full ramdisk TCG regression
`HVF_PTW_TCG_REGRESSION1` reaches a shell with 303 serial lines, zero panics,
and PC `0xfffffff02ab218bc` (60-second probe). The exact launch is recorded
in `/tmp/dvm/HVF_PTW_TCG_REGRESSION1.launch.json`.

The adapted real SPTM boot was repeated under HVF as `HVF_PTW_NATIVE_BOUND1`.
It still stops at the same SPRR write: ELR_EL2 `0x8070a3520`,
HCR_EL2 `0x488000000`, ESR_EL2 `0x02000000`, PC zero, zero serial lines.
The launch and frozen register state are recorded under that tag. The owned
guest was then quit; no display instance was touched. This is confirmation
that the new walker probe does not silently change the native boot boundary.

Python compilation and whitespace checks pass. Checkpatch reports zero
errors and one generic MAINTAINERS warning for the new experimental header;
its SPDX identifier is present. No commits or pushes were made.

```sh
python3 tools/perf/native_ptw_qemu.py --out /tmp/dvm/FRESH_PTW
python3 tools/perf/native_ptw_qemu.py --host-features --out /tmp/dvm/FRESH_PTW_HOST
python3 tools/perf/native_ptw_qemu.py --tcg --out /tmp/dvm/FRESH_PTW_TCG
```

## Remaining integration and limits

The API is a walker, not an invalidation mechanism. A production shadow
mapper must protect every source-table alias, observe CPU and DMA writes,
handle TLB maintenance, and coordinate permission changes across all vCPUs.
Callbacks cover translation descriptors, not RME granule-protection tables.
The existing two-stage `lg_page_size` result describes TCG invalidation and
must not be used as a homogeneous native mapping extent.

The current tests cover 16 KiB, little-endian stage-1 tables at EL1/EL0.
Recursive stage-2 permissions and updates, big-endian descriptors, racing
compare-exchange retries, other granules, and real SPTM/GXF state remain to
be tested. No native shadow cache, real guest table-write interception,
multicore, snapshot restoration, or full iOS HVF boot is implemented by this
change. The next integration step is to consume real SPTM tables and map
their permissions through the already tested native enforcement primitive.
