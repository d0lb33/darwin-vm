# Warm-boot Data protection-class 2 to 3 transfer

The six-CPU warm-disk cold boot hit a rejected SKS op0f request immediately
after early boot. A one-CPU control with the stock kernel and PAuth mask cache
disabled reproduced the same rejection; see the
[integration handoff](../handoff/multicpu-display.md).

`SMP_SKS_CAPTURE6` enabled only `DARWIN_SKS_REQUEST_DEBUG_CODE=0x0f`, captured
the exact failing packet and stopped before its timeout. The 164-byte packet
is `/tmp/dvm/SMP_SKS_CAPTURE6/request.bin`, SHA256
`014e378d1667d4ae1dd7e99e049a5db32d0c34d88f5ee9d2683e99b6aa8c6eb3`.

| Wire field | Value |
| --- | --- |
| +0x00 / +0x14 | header body 0x48 / IPC version 1 |
| +0x4c | migration variant 3 |
| +0x60 | UINT64_MAX |
| +0x68 / +0x6c | source class 2 / destination class 3 |
| +0x70..0x83 | zero tail |
| +0x84 | record size 28 |
| +0x90..0xa3 | exact existing Data-volume tagged record |

The existing parser accepted Data 4-to-3 only. This extends that one source
class check to accept 2-to-3, preserving packet size, destination, Data tag,
reserved-field and record-length validation. It uses the existing fake-key
variant-3 response and leaves the requested destination class intact. No new
cryptographic behavior or guest bypass is introduced.

The exact capture is embedded in `tests/unit/test-darwin-sks-migrate.c` with
an independently checked SHA256. It fails on the old parser and passes on the
change. Negative cases reject corrupted framing, wrong classes/volume, every
truncation and a trailing byte. All 19 SKS subtests and 18 host tests pass.

## Six-CPU runtime control

`SMP_SKS_FIX6_1` is a new child of the same immutable `display-warm1.qcow2`,
using the optimized binary, cache on and virtual CPU PM kernel. Its launch,
elapsed events, serial/stderr, CPU captures and framebuffer are under
`/tmp/dvm/SMP_SKS_FIX6_1/`.

Early boot completed at 9.10 host seconds. The formerly rejected transfer was
accepted at 12.56 seconds and received the normal 128-byte authenticated IPC
reply. Subsequent filesystem/key work continued; the live capture counted 40
accepted Data 2-to-3 requests and zero SKS timeout strikes. The DCP state
handshake began at 86.03 seconds, with native 2-to-3 and 4-to-5 acknowledgements
across the endpoints. A framebuffer capture remained black. These observations
prove progress past this SKS boundary, not a rendered Setup screen.

The full 300-second observation ended with zero kernel panics, zero SKS
timeout strikes, 40 Data 2-to-3 transfers and 83 directory-metadata updates.
Late serial activity was repeated display power cycling. These logs do not
establish whether the later userspace Setup migration was active. The final
1179x2556 framebuffer was entirely black. Only the owned test VM was stopped;
the shared warm parent retained its original SHA256.

Reproduce with a recorded six-CPU cold-boot launch, a new unused tag and a fresh
writable child (the runner creates it):

```sh
python3 tools/re/smp_warm_probe.py --tag NEW_SKS_WARM6 \
  --launch-template /tmp/dvm/SMP_WARM_TIMING_1V6/cpu6/launch.json \
  --parent /tmp/dvm/data-seed/display-warm1.qcow2 --seconds 300
```

The runner stops on a device rejection or kernel panic, otherwise at its
deadline. It captures CPU state/framebuffer and shuts down only its owned VM.
It does not load a RAM checkpoint or change any shared backing image.
