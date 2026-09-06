# Native AES bring-up (24A5430a, T8140)

Experimental investigation only. The production/prepared device tree hides
AES. Exposing it alone does not produce a working accelerator. An opt-in,
partial software-key model now exists; no replacement hardware keys exist.

## Why this is on the migration investigation path

`APP_FAIRPLAY_BLOCKS1/provider.tsv.blocks.tsv` proves the native initialization
helper at static `fffffff009b55a9c` waits for `IOAESAccelerator` and receives
NULL (`9b55ae4`). It returns -40 at `9b55b94`; initialization remains unset.
The subsequent provider path constructs `b3bdc913`, XORs `4c42934d`, and
returns `ffff5a5e` (-42402). This is evidence of a missing service, not missing
FairPlay credentials. See `fresh-migration-performance.md` for the observed
Passbook/passd/CoreLocation dependency chain and timing controls.

## Prerequisites, all observed in disposable fresh disk boots

`prepare_aes_probe.py` restores native matches from `/tmp/dvm/dtree_raw` into
a byte-preserving copy of the prepared tree. Nodes required:

- `/arm-io/aes`: `aes,s8000`, version 5, address-width 42.
- `/arm-io/dart-sio`: `dart,t8110`, a unique `dart-id`.
- `/arm-io/dart-sio/mapper-aes`: `iommu-mapper`, SID 1, phandle 0x71.

The original `/arm-io` base is 0x210000000. AES register windows are physical
0x38500c000/0x4000, 0x3082dc000/0x8000, and 0x308078000/0x4000. IRQ is 0x453.
The second range overlaps existing SEP RAM: `APP_AES_ROOT1/mtree.txt`.
Any model must explicitly resolve that overlap rather than silently mapping
equal-priority regions. Mapper-sio remains disabled in these probes.

`APP_AES_START1` proves successful DMA prepare/generation at 93ee6e8/93ee724.
`APP_AES_START2` proves the IOBSD wait returns at 9f16130. SecureRoot then calls
back into the accelerator (`APP_AES_ROOT1`, 93eec8c ->9f16560 ->9f16488).
Key-cache initialization 9f17064 issues a real 16-byte request at 9f1722c.

`APP_AES_KEYCACHE1` reaches power-on 93eee74 but waits for a missing platform
clock controller. AppleT8140+0xf0 is NULL; 9712bf8/9712c2c wait for it. The
native DT properties `no-clock-gate` and `no-power-gate`, checked at
9712930 and 9712970, allow an always-on diagnostic configuration. The probe
tool's `--ungated` option adds these only to the disposable tree. PMGR remains
unimplemented; this is not a PMGR fix.

`APP_AES_UNGATED1` reaches STATUS+0xc, reads zero, and panics after 7.188 active
seconds: `AppleS8000AESAccelerator::_enableAES: DPA has not been seeded!`,
AppleS8000AES.cpp:434, caller runtime fffffff0293f1764. This panic belongs to
the newly exposed diagnostic device, not the normal hidden-device baseline.

## Native register contract

All instruction addresses below are static within AppleS8000AES; prefix
`fffffff00` is omitted. Full disassembly: `/tmp/dvm/APP_FRESH1/re/aes-driver.txt`.

| Offset | Observed use | Evidence |
|---|---|---|
| 0x08 | Write 1 start, 2 stop | 93ef04c, 93eef64 |
| 0x0c | All 0x3f80 bits required for version 5 DPA readiness | 93eefd4–93eeff0 |
| 0x18 | Interrupt status, write back read bits to clear | 93eeaa0–93eeaa4 |
| 0x1c | Interrupt mask, initial 0x3effe | 93ef028–93ef03c |
| 0x20 | FIFO watermark, writes 0x40 | 93ef040–93ef044 |
| 0x24 | FIFO occupancy [15:8], capacity 128 words; stop expects bit 1 | 93f1100–93f1124, 93eef90 |
| 0x30 | Completion tag [7:0], wait while [15:8] nonzero | 93f0910–93f093c |
| 0x200 | Version >0 command FIFO | 93f118c–93f1208 |

Interrupt status bit 5 drives completion handler 93f08d4. Bit 0 wakes a
FIFO-space waiter. Error bits cause the native handler to panic; do not
invent successful completions for unsupported commands.

## First complete native command stream

`APP_AES_COMMANDS1` is a 20.055-active-second diagnostic fresh boot with
`-ignore_dpa -aes_spew`, Apple's own boot options at 93ee518 and 93ee538.
No LLDB or plugin. Neither option belongs in a performance baseline. The
DPA option still waits five guest seconds before continuing (93ef0c4 onward).
The VM was quit after capture. Serial trace and decoded JSON are under
`/tmp/dvm/APP_AES_COMMANDS1/`.

| Words | Meaning |
|---|---|
| 10910000 + eight zero words | Software key, 256 bits, context 0, encrypt, mode 1, function 0 |
| 20000000 + four zero words | IV context 0 |
| 50000010 01000100 00009c40 00011a00 | 16 bytes, source DVA 0x10000009c40, destination 0x10000011a00 |
| 80000100 | Non-interrupt flag/barrier; preserve bit 8 semantics for investigation |
| 60000100 00000010 | Store IV at DVA 0x10000000010 |
| 88000001 | Final interrupt flag, tag 1 |

The first request uses a software key. Earlier observation that key-cache
setup eventually uses hardware handles does not mean this first request
requires a fabricated UID/GID. Input plaintext was not captured; do not
assume it was zero merely because the key and IV were zero.

Native source: KEY builder 93efcbc; IV builder 93ef884; DATA builder
93f05c4/93f06d0–93f0724; STORE_IV 93f0368–93f0398; final FLAG 93f04d4.
DATA packs source bits [41:32] in word 1 [25:16] and destination [41:32]
in [9:0]. Earlier Apple AES reference layouts with 40-bit addresses must not
truncate these T8140 DMA addresses.

`tools/re/decode_aes_commands.py` parses the captured stream, excludes key/IV
payloads from output, and rejects unknown/truncated commands. Four host tests
cover this exact trace, 42-bit addresses, truncation, and unsupported formats.
All 75 host tests pass after this addition.

## Next implementation and validation boundary

The new `darwin_aes_engine.c` performs real software-key ECB/CBC using QEMU's
QCryptoCipher, and the opt-in `DARWIN_AES=software` adapter connects DART SID-1
DMA and IRQ 0x453. It accepts the observed barrier and final flags, rejects
unsupported requests without a completion, and leaves DPA status unseeded.
Its three host tests pass: zero-key AES-256 known-answer, nonzero AES-128 CBC
encrypt/decrypt split across in-place buffers with IV writeback, and unsupported
key/command/DMA rejection without completion. The full QEMU binary builds.

`APP_AES_SOFTWARE1` is a fresh diagnostic with the same native diagnostic
boot arguments as COMMANDS1 and the new opt-in model. In its first 25.202
active seconds, it completes four native requests through actual DART reads,
encryption, output/IV writes and IRQ notification. The guest submits further
requests after each completion. This is runtime progress, not just a host test
or model-generated success line in isolation. Each power-on still waits for
the unimplemented DPA readiness before the diagnostic option lets it continue.

The partial model explicitly refuses saved-state migration until pending-state
and interrupt restore support is tested; no old checkpoint silently omits its
state. Production trees and launches remain unchanged. Validate the complete
native service startup before using this in any performance comparison.
Hardware selectors, DPA readiness, and secondary key-disable registers still
need explicit contracts. Never return dummy ciphertext or invent Apple keys.

At 85.368 cumulative active seconds (25.202 +60.166), SOFTWARE1 has completed
17 native AES transactions. A read-only HMP inspection proves that native
FairPlay initialization succeeded:

```
fffffff02b878ea8: 0x0000000000000001 0x0000000000000001
fffffff02b878eb8: 0xffffffe18410a000 0xffffffe183fe7b78
```

The first two values are the overall provider initialization gate and AES
helper initialization gate; the latter pair are the service and allocated
buffer pointers. They were zero in prior unmodelled-device runs. Proof file:
`APP_AES_SOFTWARE1/fairplay-native-state85.txt`. CPU0 and CPU2 agree (CPU1
could not translate the address). This is positive native state change,
without a guest-memory write, LLDB breakpoint, injected credential, or forced
return. It resolves the previously traced missing-service initialization
path; it does not prove every subsequent FairPlay operation succeeds.

The existing early SCRD33 request is still unanswered. This run remains
diagnostic due to native AES spew, unimplemented DPA/readiness waits and
unimplemented secondary hardware-key windows. No full-migration or
whole-boot speedup claim follows from this initialization result.

Subsequent implementation: `DARWIN_AES=software` now initializes seven
internal virtual entropy slots using QCrypto's host CSPRNG at reset. STATUS
reports the native-required0x3f80 mask only if initialization succeeds.
The seven seed fields correspond to the V5 reference's six text engines and
one key-unwrap random-seeded flags; AppleS8000AES independently checks that
exact mask at93eefd4..93eeff0. Internal32-byte seed size is a virtual-model
choice, not a claimed physical register layout. This abstracts readiness;
physical power-analysis countermeasure circuitry is not emulated. No UID/GID
key or hardware self-test success bit is supplied. Failure leaves readiness
clear, and actual data operations still require the tested host AES backend.

APP_AES_READY1 tests this with both -ignore_dpa and -aes_spew removed and
unimplemented-MMIO tracing disabled. It is a fresh six-core disk boot with
no debugger or plugin. It has already repeated native slot matching/cleanup
and more than32 AES completions without a DPA warning. This eliminates the
five-second-per-power-on diagnostic delay; it must not be counted as an
improvement over the original hidden-AES production baseline. Full migration
and display timing remain the required comparison.

SOFTWARE1's next phase stops on a new native SEP failure at 129.426 cumulative
active seconds (85.368 +44.058), after 26 completed AES transactions:

```
AppleSEPXART::get_ap_slot_info: expected_bncn_len + expected_commit_hash_len == out_len
```

First panic is serial line 17678, runtime caller fffffff0295bfe34. The model
log immediately identifies endpoint 16 opcode 0x18, currently acknowledged
with zero output bytes by the generic xART fallback. This is not a secondary
panic-printer fault or proof that AES ciphertext is wrong. The VM was quit
after recording the evidence; no active diagnostic VM remains.

Native request contract, `/tmp/dvm/APP_FRESH1/re/xart-ap-slot-body.txt`:

- 95a1ecc/95a1ed4 require caller capacity >=8 BNCN bytes and >=48 commit bytes.
- 95a1f88 constructs opcode 0x18; 95a1f94 puts the slot index in request byte 6.
- 95a1f98 requests 0x38 (56) output bytes, with no input payload.
- 95a1ff0 requires exactly 56 after a successful reply.
- 95a2000 copies response byte 6 to the optional slot result.
- 95a200c copies first 8 output bytes to BNCN; 95a2018–95a2028 copies the
  following 48 bytes to commit hash.

The raw DT has `/chosen/boot-nonce` (8 bytes), `allow-ap-nonce-retrieval`, and
`/defaults/entangle-nonce`; no commit-hash property was found. This is not
sufficient evidence to invent 56 zero bytes or a guessed hash. Slot contents,
initialization/commit behavior and nonce entanglement are the next contract to
derive. Nonzero status also hits a native REQUIRE at 95a2058, so returning an
arbitrary error merely substitutes another panic.

This diagnosis has not yet improved whole-boot performance. The existing
float32 min/max optimization saves about 24% on the measured encoding span,
but fresh boot reaches its incomplete first presentation at 1026.066 active
seconds versus 1019.021 for the original. Completion requires a repeatable
material improvement in full migration/usable-display time and interactive
responsiveness, not merely driver registration or a first frame.
