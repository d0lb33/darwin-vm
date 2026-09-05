# Display development restart: six CPUs, 2026-09-04

The user's requested multicore integration is complete. The display investigation
is stopped. A fresh six-CPU system-disk boot is alive and **paused after early
boot, before any observed Setup.app launch**. Continue from this process rather
than rebooting it.

## Checkout and preserved work

- Checkout: `/Users/jdolbe1/Downloads/darwin-vm-display-multicpu`.
- Branch in both repositories: `codex/display-multicpu-integration`.
- Parent integration: `8a2b075`; QEMU integration: `6183e58`.
- Merged `origin/codex/multicpu-warm-display`: parent `e232894`, QEMU
  `480870d`. The integrated QEMU source tree matches that upstream tree.
- Original display checkout and its uncommitted changes remain untouched.
  Preserved copies were committed in this checkout before merging.
- Exclave bootstrap changes and their tests are retained. This run uses the
  existing migrated image; it does not establish that the full Exclave payload
  has been added to that image. See `docs/re/exclave-assets.md`.
- The previous single-CPU investigation was saved and its VM stopped:
  `/tmp/dvm/checkpoints/DISPLAY_SETUP_CONNECTED25/manifest.json`.
  Do not restore its RAM into a six-CPU VM. Prior display findings are in
  `docs/re/surface-cache-and-completion.md`.

## Live run

- Tag: `DISPLAY_SMP6_START2`; owned QEMU PID at capture: **33683**.
- Monitor: `/tmp/dvm/DISPLAY_SMP6_START2.sock`.
- QMP: `/tmp/dvm/DISPLAY_SMP6_START2.qmp`.
- UART: `/tmp/dvm/DISPLAY_SMP6_START2.uart`.
- Guest GDB: `127.0.0.1:1387`.
- Exact launch: `/tmp/dvm/DISPLAY_SMP6_START2/launch.json`.
- Disk: `/tmp/dvm/DISPLAY_SMP6_START2/disk.qcow2`, fresh writable child of
  `/tmp/dvm/data-seed/root-welcome-checkpoint1.qcow2`.
- Fast QEMU build with assertions, `-smp 6 -accel tcg,thread=multi`, 12 GiB.
- `DARWIN_SMP_PV=1`, `DARWIN_PAUTH_CACHE=on` (not verify mode).
- Hash-checked SMP kernel: `/tmp/dvm/DISPLAY_SMP6.bootkc`.
- Existing display DT, trustcache, boot arguments and DCP environment preserved.
- Cold boot only; no RAM checkpoint loaded.

Check status, then resume when development continues:

```sh
cd /Users/jdolbe1/Downloads/darwin-vm-display-multicpu
python3 tools/hmp.py /tmp/dvm/DISPLAY_SMP6_START2.sock 'info status'
python3 tools/hmp.py /tmp/dvm/DISPLAY_SMP6_START2.sock cont
```

Do not rebuild while this guest is running. Use `stop` on the same monitor to
pause it. Multicore checkpoint restore remains unvalidated; this session leaves
the live guest paused instead of claiming a validated six-CPU RAM checkpoint.

## Verification

The condition-bounded boot paused at the fixed Data class 2-to-3 SKS transfer
after **12.173 host seconds**. The log records native request acceptance and
the returned migrated record. Early boot completed. All six CPU register sets
show initialized execution contexts, including userspace execution on CPU 1.
No kernel panic, SKS op0f rejection, or `set_dir_stats` attribution pass was
observed through this boundary. This is an early boot check, not a full-boot
benchmark or a rendered Welcome screen result.

Evidence under `/tmp/dvm/DISPLAY_SMP6_START2/`:

- `boot-result.json`, `validation.json`, `cpu-registers.txt`, `cpus.txt`;
- `provenance.json`: commits, kernel and seed hashes, unchanged seed hash and
  size/mtime metadata for all 26 backing-chain layers;
- `launch.json`, `qemu.pid`, `disk.qcow2`.

Serial and device logs are `/tmp/dvm/probe/DISPLAY_SMP6_START2.serial.log` and
`/tmp/dvm/probe/DISPLAY_SMP6_START2.stderr.log`.

Build completed; 19 SKS tests, 2 AFK tests and 23 host tests passed. Shell syntax
and diff whitespace checks passed. Logs: `/tmp/dvm/DISPLAY_SMP6.build.log`,
`/tmp/dvm/DISPLAY_SMP6.{sks-tests,afk-tests,host-tests}.log`.

An initial short run, `DISPLAY_SMP6_START1`, stopped at the first AP driver
message and was not retained after its probe launcher exited. START2 uses a
detached process session with file-backed output; it was independently checked
alive, reparented to PID 1, paused and consuming 0% CPU after launch completion.
