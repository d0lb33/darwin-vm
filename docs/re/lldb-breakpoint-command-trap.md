# LLDB breakpoint-command trap and corrected display probe

The first `UI_BKD_WS_GATE1` result did not prove that backboardd skipped its
window-server task.  The LLDB command file used repeated `-o` options on one
`breakpoint command add` invocation.  LLDB 21 accepted and displayed the
commands, but the resulting Python callback did not produce the expected
markers during the boot (`/tmp/dvm/UI_BKD_WS_GATE1.lldb.log:14-78`).  The
same file did show every breakpoint resolved, followed only by the final
SIGINT (`:80-93`).  Treat those zero hit counts as a tooling failure, not a
runtime localization.

The reliable form is a single Python callback function imported with
`command script import`, then installed with one `breakpoint command add -F`
per breakpoint.  The corrected run used
`/tmp/dvm/bkd_ws_callbacks.py` and the command file
`/tmp/dvm/UI_BKD_WS_GATE1.correct.lldb`; LLDB records the callback form at
`/tmp/dvm/UI_BKD_WS_GATE1.lldb.log:14-44`.  A callback must return `False` (or
otherwise leave the process stopped/continue explicitly as intended); do not
put several shell-style `-o` callback fragments on the same command line.

## What remains valid

The same-boot dyld slide and raw cache positive control in
`docs/re/quartz-first-surface-runtime.md` remain valid: the resolver directly
observed a uniquely matched cache instruction at runtime PC `0x1a1b3ed68`,
static PC `0x181cf2d68`, with slide `0x1fe4c000` (that document's “Same-boot
dyld-cache slide proof”).  The independent RPC trace, black screendump, and
persistent-NVMe/no-panic witnesses also remain valid.  The no-D575 run still
has zero D575, A407, A408, D589, D591, and surface-map records.

The following conclusions are invalid and must not be repeated:

- QuartzCore's zero-hit table is not a definitive localization.  The
  breakpoint experiment hid 69 matching stops, so zero visible callback
  markers cannot prove that CADisplay discovery or the first-surface path was
  never entered.
- The D575-bool0 run's “all swap breakpoints zero” claim is likewise invalid:
  one matching stop was hidden by the same command trap.  Its transport
  completion, black frame, storage/boot health, and independent RPC absence
  remain evidence; the hidden stop means only that the exact swap stage must
  be rerun with valid callbacks.

## Backboardd static gate

The exact same executable supplies a useful static control.  Its PIE
`__TEXT` base is `0x100000000`; the main task registers the literal `window
server` at `0x100018598-0x1000185b4`, whose global block at
`0x10008c4b8` invokes `0x100018e88`.  That once wrapper uses predicate
`0x1000aa418` and dispatches the block at `0x10008edc8`; its authenticated
invoke pointer is `0x1000257cc`, the `StartWindowServer` body
(`docs/re/backboardd-start-window-server-gate.md:17`, `:23-31`).  This is the
same-image proof that the task block and its dispatch-once path are connected;
it is not a runtime-hit claim.

Inside `StartWindowServer`, `+[CADisplay mainDisplay]` leaves the result in
`x21`, tested for null at `0x1000259fc`; the CAWindowServer display is in
`x22`, tested at `0x100025a00`.  Null values reach the explicit headless path
at `0x100025df0`; non-null values reach “StartWindowServer: Setup complete”
at `0x100025f74-0x100025f88` (same doc, `:34-40`).

## Corrected staged breakpoint plan

Resolve the backboardd PIE base from the same boot, then use valid Python
callbacks in this order:

1. `B+0x18e88`; read predicate `B+0xaa418`.
2. `B+0x257cc`; prove the once block entered `StartWindowServer`.
3. `B+0x259fc` and `B+0x25a00`; record `x21` and `x22`.
4. `B+0x25fdc` and `B+0x25f74`; distinguish headless from setup-complete.
5. Only after those hits, arm the QuartzCore discovery and IOSurface edges,
   then H17P submit/map/A407/A408 edges.

Each stage must preserve its callback output and the matching serial log
literal.  A zero count is reportable only when the callback mechanism has
first been validated by a known hit in that same boot.
