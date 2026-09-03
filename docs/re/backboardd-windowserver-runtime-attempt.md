# backboardd WindowServer runtime attempt

## Scope and configuration

`UI_BKD_WS_GATE1` was a read-only runtime diagnostic of the
`backboardd` WindowServer startup gate on iOS 27 beta 8 (24A5430a), target
iPhone17,3 / H17P (t8140).  It booted the established persistent-NVMe parent
through a fresh qcow2 child, with IOMFB level 4 and only the measured D120 and
D586 callbacks.  D575 was omitted.  No source or base disk image was
modified; all guest writes went to disposable fresh qcow2 children.

The completed control boot ran for 180 seconds.  Its full serial and model
logs are `/tmp/dvm/UI_BKD_WS_GATE1.attempt0.serial.log` and
`/tmp/dvm/UI_BKD_WS_GATE1.attempt0.stderr.log`.  A screendump taken before the
exact-tag guest was stopped is `/tmp/dvm/UI_BKD_WS_GATE1-live.ppm` (converted
PNG: `/tmp/dvm/UI_BKD_WS_GATE1-live.png`).

## Same-run process and image evidence

A frozen 12-GiB RAM capture from that completed boot is preserved at
`/tmp/dvm/UI_BKD_WS_GATE1.mem/`.  It supplies two independent records for the
live process:

- The active `proc` record is in `010000000000.bin`.  Its PID/state pair at
  file offset `0x2bdbf948` is `47 00 00 00 02 00 00 00`, meaning PID 71 and
  `SRUN` (`p_stat=2`).  The adjacent `p_comm` and `p_name` fields are at
  `0x2bdbfe44` and `0x2bdbfe55`.  The executable UUID at `0x2bdbfe7c` is
  `DD9BBEFE-22E3-3073-B3C5-456636A54786`, matching the local arm64e
  `/Users/jdolbe1/dvm-artifacts/extract/bin/backboardd`.
- The same capture contains a complete dyld process catalog at file offset
  `0x3357c20a`, length `0x12fe`.  The decoded dictionary names PID 71, assigns
  `/usr/libexec/backboardd` runtime `__TEXT` base `0x1027fc000`, records its
  static preferred address as `0x100000000`, and carries the same UUID.  The
  same record assigns this boot's shared cache base `0x19ad38000` and UUID
  `58C54E82-C171-300E-AEEE-06DF937AA565`.  The exact extracted catalog is
  `/tmp/dvm/UI_BKD_WS_GATE1.backboardd.plist`; the machine-readable decode is
  `/tmp/dvm/UI_BKD_WS_GATE1.catalog-parse-test.json`.

This establishes a defensible PIE base for the completed boot, but not for a
later boot.  A second frozen control,
`/tmp/dvm/UI_BKD_WS_GATE_MAP1.mem/010000000000.bin`, decoded PID 71 with the
same executable UUID but base `0x1001c0000` and shared-cache base
`0x185b54000` (`/tmp/dvm/UI_BKD_WS_GATE_MAP1.catalog.json`).  Both slides
therefore vary across fresh boots and must be measured per boot.

## Runtime-probe outcome and tooling correction

The first control sampled 20,286 EL0 PCs from reset while searching for an
exact eight-instruction match in the extracted `backboardd`, but did not
sample the process while it was executing.  The complete negative resolver
record is `/tmp/dvm/UI_BKD_WS_GATE1.backboardd-slide.json`.  This is not a
validated non-hit at `StartWindowServer`: no correctly slid breakpoint was
armed in time, so it says nothing about the headless branch.

A later tooling-only child tested the corrected LLDB command mechanism.
Every breakpoint used the Python callback
`bkd_ws_callbacks.callback(frame, bp_loc, internal_dict)`, which prints its
marker, registers, runtime code, Mach-O header/UUID, and static-byte
comparison, then returns `False` to continue.  The debugger log proves the
callback was installed for all ten locations before `continue`:
`/tmp/dvm/UI_BKD_WS_GATE1.tooling-control.lldb.log:41-143`.  This replaces the
older invalid pattern of repeating `breakpoint command add ... -o`, which
retained only the final command.

That tooling child reused the previous boot's PIE and shared-cache bases.
Before any hit was interpreted, a live HMP check found both reused addresses
unmapped in the stopped context.  Because the independent catalogs above
prove slide variation, the child was immediately reclassified as a tooling
control and exact-stopped.  Its zero hits are deliberately not treated as
semantic evidence.  Its short screendump,
`/tmp/dvm/UI_BKD_WS_GATE1-tooling-control.ppm`, was also black.

The unresolved experiment is therefore narrow: pause a fresh boot after its
current process catalog is available but before the registered `window
server` startup task runs, decode that boot's `backboardd` and shared-cache
bases, positive-control the Mach-O magic/UUID and exact instruction bytes,
then arm the corrected callbacks at the PIE-relative gates documented in
`backboardd-start-window-server-gate.md`.  No conclusion about `x21`, `x22`,
the headless path, or setup-complete can be drawn from this attempt.

## Boot health and display result

The completed control boot preserved the storage/display baseline:

- XNU selected `disk1s1` as root at serial line 313.  It found Data
  `disk1s2` at lines 424 and 447 and mounted it encrypted at lines 475 and
  486-487.  Hardware mounted at lines 493-496; encrypted User mounted at
  lines 577 and 586-587.
- `Early boot complete` is serial line 639.  The log contains zero first
  `panic(cpu` records and zero `Copying ` lines.
- The real user client reported internal display type 0 at serial line 735.
  The IOMFB trace completed A401, A353, D120, and D586 at stderr lines
  171-293.  It contains no D575, A407, A408, `surface_map`, or `swap_submit`.
- The PPM has a 9,048,240-byte pixel payload containing only byte value zero;
  it is a genuinely black framebuffer, not a Welcome/Hello capture.

Artifact hashes:

| Artifact | SHA-256 |
| --- | --- |
| `UI_BKD_WS_GATE1.attempt0.serial.log` | `7328f7cf3bc08f81772080f95ba2ffc28350b6ac9bcf2b582050515d65813202` |
| `UI_BKD_WS_GATE1.attempt0.stderr.log` | `d6ef0357769c8eb4d89dfa3a96f9a0491b06ff261ddf3b7e1f415a496cb24c3a` |
| `UI_BKD_WS_GATE1.backboardd.plist` | `a753718ba9df11b40c073e8fb227f3cba9b40917dad2a45cd0b4ea1dde49bddb` |
| `UI_BKD_WS_GATE1-live.ppm` | `43d418d35e149ea7e071c60ecb4ce967addd0aa4fc2eff4c6b0276403ed3f7fb` |
| `UI_BKD_WS_GATE1-live.png` | `751b436d4a028fa873ce9bbc5bbac0d943765aff73631917b3b48230117798ed` |
| `UI_BKD_WS_GATE1.backboardd-slide.json` | `38dd9c3481e4b0dc36e44b9079e533f1b22c43181af3421da52fe6cce854767d` |
| `UI_BKD_WS_GATE1.tooling-control.lldb.log` | `0ffb64cceeee7018df225ef5942654f88df675b042eb3b4f4916a102341e3e27` |
| `UI_BKD_WS_GATE1-tooling-control.ppm` | `43d418d35e149ea7e071c60ecb4ce967addd0aa4fc2eff4c6b0276403ed3f7fb` |
