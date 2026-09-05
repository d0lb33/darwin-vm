# Class-13 empty-record file-key unwrap on a six-CPU cold boot

`DISPLAY_SMP6_WARM2` cold-boots the disk captured by
`DISPLAY_MIGRATION_RETURN17_TIMING`, after flattening its backing chain and
installing the matching Exclave payload. It does not restore one-CPU RAM.
The immutable base and provenance are under
`/tmp/dvm/data-seed/migrated-exclave-smp6/`; `exclave-merge.json` verifies
1,672 installed files/links. The bootstrap image phase already invokes
`phase_exclave` in `tools/rootfs/bootstrap_data_volume.sh`.

At `DISPLAY_SMP6_WARM2.stderr.log:5782`, the SEP model rejects an op09
108-byte empty-record request with protection class 13. Its serial log then
records SKS timeout strikes. Physical task inspection finds SpringBoard and
a migration plugin waiting in synchronous preference reads. This is a model
protocol gap, not evidence that CPU parallelism is ineffective.

## Exact request

`tools/re/dart_read.py` read the paused endpoint-18 OOL buffer through HMP.
The witness `/tmp/dvm/DISPLAY_SMP6_WARM2.op09-class13.bin.json` records:

- SEP DART MMIO `0x280b80000`, stream 0, TCR 1, TTBR `0x1001c241`.
- DVA `0x1000000c000` maps to PA `0x1001a3d8000`.
- Request size 108; header body size `0x48`, IPC version 1, variant 1.
- Protection class 13 at wire `+0x60`, record length 0 at `+0x64`,
  output selector 2 at `+0x68`.
- SHA-256 `20a77bfcf92a0288cb13070e23d9fbc8609320b8fc0037389329131aa1da8054`.

The old rejection diagnostic printed selector 0 because class validation
failed before the selector was loaded. The parser now reports the actual
wire selector on rejected requests of either known size.

Only the captured short class-13 form is newly accepted. The wrapped-record
class-13 form remains unsupported. The existing selector-2 response codec
and fake-key semantics are unchanged. The unit fixture pins the exact bytes
and tests corrupted fixed fields, unsupported class, all truncations, trailing
bytes and the unobserved long class-13 form. All 21 SKS tests pass in
`/tmp/dvm/DISPLAY_SMP6.class13-tests.log`; 23 host tests pass in
`/tmp/dvm/DISPLAY_SMP6.host-tests.log`.

## Runtime control

`DISPLAY_SMP6_WARM3` uses the corrected fast executable, a fresh child of the
same immutable migrated/Exclave base, six CPUs, MTTCG, and PAuth cache on.
Its serial log reaches Early boot complete at guest time 8.576505 seconds
(line 642). The initial `tools/probe.sh` verdict is zero XNU panics, stopped
on the verified shared-cache boundary and continued under LLDB.

`/tmp/dvm/probe/DISPLAY_SMP6_WARM3.stderr.log:4872-4875` records acceptance
of the class-13 op09 request, ID 87, and its 128-byte authenticated response.
Subsequent requests succeed, showing the queue was consumed. SpringBoard
reaches the BKS migration check-in, witnessed in
`/tmp/dvm/DISPLAY_SMP6_WARM3.events/progress.BKS_CLIENT_REMOTE_CALL.json`.
The old preference-read stall is no longer the observed SpringBoard stack.

The later plugin wrapper stack calls
`os_eligibility_bring_up_daemon_4_migration` (static `0x2c211f334` in
libsystem_eligibility, shared-cache slide `0x14f94000`). Its eligibilityd
worker is runnable inside ICU locale initialization at this inspection.
This is ongoing migration work; neither migration completion nor display
output has yet been demonstrated in this six-CPU run.

The WARM2 stopped state is preserved at
`/tmp/dvm/checkpoints/DISPLAY_SMP6_WARM2_SKSBLOCK/manifest.json`:
3,351,266,215 VM-state bytes, migration 2.040 seconds, total paired capture
11.759 seconds. The mailbox's retained last message had already become a
SCRD message (`0x0028040a`), so its failed SKS correlation ID was not guessed
for replay. WARM3 is the clean cold-boot control.
