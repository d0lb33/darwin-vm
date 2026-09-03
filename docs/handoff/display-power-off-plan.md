# Handoff: get the DCP power-off past AppleFirmwareKit, then SpringBoard past `mainDisplay`

Written 2026-09-03 for a smaller agent. Follow it in order. Every step has a
command, what its output must contain, and what to do if it does not. Stop and
report at any **ESCALATE** line; do not improvise past one.

## Ground rules (from CLAUDE.md, repeated because they bite)

- Never push. Commit only when told.
- Boot with `tools/re/setup_gate_probe.sh <TAG>`. Before every boot:
  `pkill -f 'unix:/tmp/dvm/<PREVIOUS_TAG>.sock'`, or it fails with "port 1234 is in use".
- The default parent disk is `/tmp/dvm/data-seed/persistent-parent.qcow2`. If it is
  missing (host rebooted), rebuild it with `tools/rootfs/rebuild_persistent_parent.sh`
  (about 10 minutes) and read `tools/rootfs/README.md` first.
- Do not edit `qemu-sptm/hw/arm/darwin.c`, `darwin_asc.c`, `darwin_aic.c`,
  `dt_fixup.py`, `run.sh`, `CLAUDE.md`. If a fix needs one of them, write the exact
  diff into your report instead.
- Build with `cd qemu-sptm/build && make -j18` and never while a boot is running.
- A boot that prints `panic(cpu` in `/tmp/dvm/probe/<TAG>.serial.log` is a
  regression unless the first panic line is `Halt/Restart Timed Out`.

## What is already established (do not re-derive)

Read `docs/re/setup-launch-runtime.md`, sections "The kernel wait" and
"Correction: the wait is a lock". Short form:

1. SpringBoard hangs in `+[CADisplay mainDisplay]` because backboardd's render
   server holds `IOMFBServer+0x3b0` while `IOConnectCallMethod` selector 79
   (`GetBlock` kind 0x41) never returns.
2. That call blocks on the IOMFB power object's recursive lock, held by the IOMFB
   work-loop thread running the display-off `set_device_power(0)`.
3. That thread used to block in RTBuddy's guard because the DCP's boot power-on
   never finished (TraceKit endpoint 0x0a unanswered). **Fixed** in
   `darwin_asc.c` (`rtk_handle_tracekit`), commit "darwin-asc: answer RTBuddy
   TraceKit endpoint 0x0a".
4. It now blocks one stage later, inside `AppleFirmwareKit` on an `SMMachine`
   object (kernel `IORecursiveLockSleep`), while the AP sends nothing more to the
   DCP after RPC `A500`. Suspect: the 13 EPIC service proxies that
   `DARWIN_DCP_EPIC=all` makes bind; several sit in interface start waiting for
   events our model never sends, and they likely hold AFK power assertions.

## Step 0 — result of the control run (read 2026-09-03 16:30, so Step 1 is already answered)

`UI_NOEPIC2` (`DARWIN_DCP_EPIC=off`) still hangs: 39 `IOCONNECT_CALL_METHOD`
entries, 38 returns, and the unmatched entry is `x1=0x4f` (selector 79) at
`elapsed=104.1s` (`/tmp/dvm/UI_NOEPIC2.lldb.log`, hit 38). 0 panics. So the EPIC
proxies are **not** what the AFK state machine waits for. **Start at Step 2** on the
guest that run left frozen at `/tmp/dvm/UI_NOEPIC2.sock`; if the host has rebooted
since, rerun the boot as described in Step 1 and then do Step 2.

## Step 1 — read the control run that was already started

Run tag `UI_NOEPIC2` booted with `DARWIN_DCP_EPIC=off` (no EPIC services announced).
Its guest may still be frozen on `/tmp/dvm/UI_NOEPIC2.sock`.

```
grep -v PROOF /tmp/dvm/UI_NOEPIC2.lldb.log | grep -o '=== IOCONNECT_CALL_METHOD\(_RET\)\? hit' | sort | uniq -c
grep -v PROOF /tmp/dvm/UI_NOEPIC2.lldb.log | grep -A1 'IOCONNECT_CALL_METHOD hit' | grep -c 'x1=0x4f'
```

- If the `_RET` count equals the entry count, selector 79 returned: **the EPIC
  users were the blocker.** Go to Step 3.
- If entries exceed returns by one and the unmatched entry has `x1=0x4f`, the
  hang is still there. Go to Step 2.
- If the run is missing or panicked, rerun it:
  `CALLBACKS=display_iokit_callbacks SECS=480 DARWIN_DCP_EPIC=off tools/re/setup_gate_probe.sh UI_NOEPIC3`
  (about 9 minutes; it leaves the guest frozen).

## Step 2 — name the next blocker (only if Step 1 still hangs)

With the guest frozen:

```
python3 tools/re/stall_postmortem.py /tmp/dvm/<TAG>.sock <TAG> \
  --kext AppleFirmwareKit --kext driver.RTBuddy --kext IOMobileGraphicsFamily --kext AppleDCP
```

It writes `/tmp/dvm/<TAG>.postmortem.txt`: one block per kernel stack that
references those kexts, with the thread's `wait_event`. Find the block whose
frames include `IOMobileGraphicsFamily-DCP+0x23b50` (that is the display-off
thread). Report, verbatim, its frame list and `wait_event`, plus the block for the
thread whose frames include `IOMobileGraphicsFamily-DCP+0x23abc` (the GetBlock
caller). If the display-off thread's deepest kext frame is still
`AppleFirmwareKit+0x3102c`, the AFK state machine is still waiting even without
EPIC users. **ESCALATE** with those two blocks; do not try to reverse `SMMachine`.

If the deepest frame is somewhere new, read the object at `wait_event` with
`python3 tools/re/kmem.py /tmp/dvm/<TAG>.sock dump <wait_event minus 0x40> 24`
and include that dump in the report. **ESCALATE.**

## Step 3 — confirm SpringBoard moves (only if Step 1 succeeded)

Boot once more with the SpringBoard callbacks:

```
pkill -f 'unix:/tmp/dvm/UI_NOEPIC2.sock'
CALLBACKS=sb_setup_path_callbacks SECS=600 DARWIN_DCP_EPIC=off tools/re/setup_gate_probe.sh UI_SB_NOEPIC1
python3 tools/re/lldb_hits_summary.py /tmp/dvm/UI_SB_NOEPIC1.lldb.log | head -40
```

Success is a `SB_ADFL_ENTRY` hit (SpringBoard's `applicationDidFinishLaunching`).
Also grep the serial log for `SpringBoard` and `Setup`. Report the hit table
either way.

- If `SB_ADFL_ENTRY` fires: take a screen capture immediately, before anything
  else: `python3 tools/hmp.py /tmp/dvm/UI_SB_NOEPIC1.sock screendump /tmp/dvm/UI_SB_NOEPIC1.png -f png`
  and attach it. Then **ESCALATE** with the summary; the next work (framebuffer
  allocation, swaps, pixels) needs the DCP pixel path and is not in this plan.
- If it does not fire but selector 79 returned: run Step 2's post-mortem on this
  guest and report which thread now holds `IOMFBServer+0x3b0`
  (`display_wait_callbacks` from the runtime notes shows how it was found
  before). **ESCALATE.**

## Step 4 — record what you learned

Append a dated subsection to `docs/re/setup-launch-runtime.md` with: the tag names,
the two grep results from Step 1, and the frame blocks from Step 2 or the hit
table from Step 3. Every claim carries a log path and line number. Do not edit
CLAUDE.md.

## Things that look like failures and are not

- `reached shell: no` in the probe verdict is normal for system-volume boots.
- `TXM [Error]: selector: 38 | 42` is noise; strip it with `grep -av 'TXM \[Error\]'`.
- `Couldn't alloc class "AFKFirmwareService"` at boot is normal.
- A zero-serial-line boot means `firmware/` is missing from the worktree; symlink it.
