# Home-screen freeze: User protection class 3 to 2

`TOUCH_INPUT_R18` restored `DISPLAY_SMP6_HOME13` with six CPUs and displayed
the native Home screen. Its first panic is at serial line 4241:
`AppleSEPManager panic for "AppleSEPKeyStore": sks request timeout`.
The preceding model rejection at stderr line 24242 is op0f, 164 bytes,
variant 3, destination class 2. The renderer retained its last frame; QEMU's
`running` status did not establish that iOS was alive.

`TOUCH_INPUT_R19` restored the same healthy checkpoint in 2.703 seconds,
enabled `DARWIN_SKS_REQUEST_DEBUG_CODE=0x0f`, and paused on the rejection with
`tools/re/live_log_guard.py` before timeout. The exact bytes are in
`/tmp/dvm/TOUCH_INPUT_R19.request.bin`, SHA256
`4a03fd55ebd836a784a4cac798e288152bfcc604bdd2f0f7ca60ded3d721d9de`.
The source log is
`/tmp/dvm/checkpoints/DISPLAY_SMP6_HOME13/restores/TOUCH_INPUT_R19/qemu.stderr.log`,
lines 27507–27519. Guard evidence is `/tmp/dvm/TOUCH_INPUT_R19.guard.json`.

The request has source class 3 at +0x68, destination class 2 at +0x6c,
record length 28 at +0x84, and the existing User-volume tag at +0x90.
The native `fs_migrate_media_key_to_class` bridge at
`0xfffffff0095730a0..0xfffffff00957311c` establishes the source/destination
fields and returned class; see `sks-op0f-media-key-migration.md`.

The parser adds this one observed pair to the existing tagged User variant.
It preserves all framing, reserved-field, tag and length checks, and uses the
existing authenticated fake-key response with the requested destination.
The exact capture fails before the change and passes afterward. All 23 SKS
tests pass, including damaged fields, unsupported class 5, every truncation
and a trailing byte. The prior class-2 negative case becomes class 5 because
class 2 is now observed.

The rebuilt model accepted the same tagged User class-2 variant on the warm
six-CPU run `TOUCH_INPUT_R21` (stderr lines 22693–22694). A return probe at
`0xfffffff02957bc18` recorded `SKS_OP0F_RETURN`, caller `healthappd`, `x0=0`,
and the native decoded output length 40 and class 2 at `sp+0x148`.
The durable trace is `/tmp/dvm/TOUCH_INPUT_R21.lldb.log`, timestamp
1788576272.2827442. Home continued rendering afterward. This validates the
observed packet and native return; it does not establish general SEP
completeness, touch delivery or idle policy.
