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

## Verified 2026-09-02, after the ANS NVMe controller landed

Same four configurations, re-run against a build carrying `darwin_ans.c`
(probe tags `A9REG_PLAIN`, `A9REG_DCP`, `A9REG_SEP`) — no change:

| Config | probe | Result |
|---|---|---|
| baseline (no `-enable`) | `--secs 90` | 0 panics, reaches shell |
| `-enable dcp` | `--secs 120`, `DARWIN_ASC_DEBUG=1` | 0 panics, reaches shell, **11/11** AFK endpoints started |
| `-enable sep` | `--secs 120` | 0 panics, reaches shell, **0** `timed out waiting for` |

And the new ones. All of these need `tools/ans/dt_ans_fixup.py` in place of
`dt_fixup.py` until the one-line `fixup_iops()` fix lands, and
`DARWIN_ANS_SELFWIRE=1` until `darwin.c`'s own call is boot-tested:

| Config | probe | Result |
|---|---|---|
| `-enable ans -enable smc`, restore ramdisk + 21 GB image on `-drive id=ans` | `--secs 200` | 0 panics, reaches shell, `IOMediaBSDClient` registered, APFS mounts the container (`nx_mount:1509: disk0 stable checkpoint indices: desc 108 data 2344`) — tag `A9FW` |
| same, `DARWIN_UNIMP_DEBUG=1` | `--secs 220` | **0** `unimp:` access lines anywhere, so nothing under `/arm-io/ans` or `/arm-io/sart-ans` falls through — tag `A9UNIMP` |
| system volume rooted **off ANS**: `-enable ans -enable smc -ephemeral-data -dram 12G`, boot-args `rootdev=disk1s1 ignition_level=1 launchd_unsecure_cache=1` | `--secs 900 --mem 12G` | 0 panics, reaches `Early boot complete. Continuing system boot.`, `mount-phase-2` **completes** — tags `A9LONG2`, `A9FINAL`. See `docs/re/ans-nvme-references.md` §12 |

Known-bad combination, do not chase it as a regression: `-enable sep` **plus**
the system volume rooted off ANS panics in
`AppleSEPXART::getFullEpochs()`, `REQUIRE fail: expected_out_len == out_len
@AppleSEPXART_embedded.cpp:1021` (tag `A9LONG`). `-enable sep` on the restore
ramdisk is unaffected.

Two things to check in the stderr of any ANS run:

- `ans(ans): SART does not allow ... (N so far)` is expected and harmless; see
  `ans-nvme-references.md` §11.5. It is rate-limited, so a run that produces
  hundreds of thousands of them again means the limiter broke.
- `Assertion failed: (child->perm & BLK_PERM_WRITE)` means the block backend
  lost its write permission. It only shows up on the *first guest write*, which
  can be minutes into a boot.

## Markers worth grepping

- `rootdev patch:` and `txm log patch:` in the stderr log - both kernel patches
  applied. A `refusing to patch` warning from either is a real finding.
- `grep -c 'TXM \[Error\]'` should be **0**.
- `probe.sh` reports `reached shell: no` for system-volume boots. That is
  correct and not a regression: it greps for `can't access tty`, which only the
  restore ramdisk emits. The system volume runs launchd instead.
