# Regression matrix

Configurations we expect to keep working, and how to check them.

## How to run this correctly

**Do not rebuild `qemu-sptm/build/qemu-system-aarch64` while a sweep is in
flight.** A sweep on 2026-09-02 reported a critical regression - kernel data
abort at `pc 0xfffffff02b271a10`, 7 of 8 configurations failing - that did not
reproduce at all. Its first failing boot started at 05:05:56 and the QEMU binary
was relinked at 05:05:57: it was booting a binary being rewritten underneath it.
Every one of those results was an artifact.

Also give each configuration enough time. `reached shell: no` at `--secs 75` for
a DCP tree means the window was too short, not that anything broke; the DCP
chain needs ~130s. The timeouts below are ones that actually work.

## Verified 2026-09-02, after the AFK/EPIC merge, the two kernel patches and the ASC staging queue

| Config | probe | Result |
|---|---|---|
| baseline (no `-enable`) | `--secs 75` | 0 panics, reaches shell |
| `-enable dcp -dram 8G` | `--secs 140`, `DARWIN_ASC_DEBUG=1` | 0 panics, reaches shell, **11/11** AFK endpoints started |
| `-enable dcp` + `DARWIN_DCP_EPIC=all` | `--secs 140` | 0 panics, reaches shell, 11/11 endpoints, 0 FIFO overflows, `DCPAVRemoteSACControllerProxy failed to start` present |
| `-enable smc` | `--secs 75` | 0 panics |
| `-enable sep -dram 8G` | `--secs 110` | 0 panics, reaches shell, `AppleSEPManager::start: control endpoints created` |
| `-dram 40G -ephemeral-data -skip-keybag`, system volume | `--secs 900` | 0 panics, reaches `Early boot complete`, 0 read-only-fs errors, 0 persona failures |

`-enable ans` is a known failure, not a regression: SPTM stops at
`sart_sanity_check_throttles: Sart invalid throttle cfg [0] = 0x0`. Tracked
separately.

## Markers worth grepping

- `rootdev patch:` and `txm log patch:` in the stderr log - both kernel patches
  applied. A `refusing to patch` warning from either is a real finding.
- `grep -c 'TXM \[Error\]'` should be **0**.
- `probe.sh` reports `reached shell: no` for system-volume boots. That is
  correct and not a regression: it greps for `can't access tty`, which only the
  restore ramdisk emits. The system volume runs launchd instead.
