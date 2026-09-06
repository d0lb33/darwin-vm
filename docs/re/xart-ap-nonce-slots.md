# xART AP nonce slots, T8140 / 24A5430a

This corrects a historical misidentification in `sep-xart-epochs.md`: opcode
0x15 generates an AP nonce, not an epoch slot. The eight-byte reply length
was right, but a zero constant is not a nonce lifecycle implementation.

## Native chain and identities

AppleMobileApNonce917683c calls stub9179090 ->SEPApNonce9583b38 ->
AppleSEPXART95a1b60, whose block95a1bf0 builds opcode15 and requests8 bytes.
`_generateNonce` then hashes/entangles the returned nonce at9176868. The SEP
returns the raw nonce; AP-side hashing is a separate operation.

`_getBootedSlotAndState`9176e58 retrieves the `sidp-rom-manifest-hash` property
at9176eec and compares that OSData against each slot's48-byte commit hash
at9176fbc. On equality,9177064/917706c returns slot ID and state. Both the raw
and prepared DT have48-byte `/chosen/sidp-rom-manifest-hash` and8-byte
`/chosen/boot-nonce`. The commit hash is not a guessed SHA384 of the nonce.

The native iterator queries slot IDs0 and1 (9176f50..9176fd0, table761dd70).
`_pickNewNonceSlot`917777c accepts booted states2 or3 and picks the other slot.
`_cleanupNonceSlots` identifies state3 as L (9176d08..9176d2c), state2 as RL
(9176d04..9176d70). It skips cleanup when L's other slot is proposed state1
(9176d38). An empty other slot has state0 and needs no deletion (9176d7c).

Delete:9176db8 ->9179050 ->9583b80 ->95a2064 ->95a20e4 ->opcode19.
Promote:9176df4 ->9179080 ->9583ba4 ->95a2134 ->95a21b4 ->opcode1a.
Promotion follows an RL boot; the L state is the resulting promoted state.

Get slot:9176f7c ->9179070 ->9583b5c ->95a1ebc ->95a1f5c ->opcode18.
Request slot index is byte6. Reply has exactly56 OOL bytes,8-byte raw nonce
followed by48-byte commit hash; response byte6 is state, not slot ID.
Nonzero status and wrong lengths both hit native REQUIRE assertions.

Evidence disassemblies under `/tmp/dvm/APP_FRESH1/re/`: `apnonce.txt`,
`xart-slot-caller.txt`, `xart-create-ap.txt`, `xart-ap-slot-body.txt`,
`xart-commit-clear.txt`. The actual zero-length reply panic was
APP_AES_SOFTWARE1/serial.log:17678, at129.426 cumulative active seconds.

## Experimental model

`DARWIN_XART_AP_SLOTS=1` seeds virtual slot0 as the already booted/promoted
image, using the supplied nonce and manifest hash verbatim, and slot1 empty.
Assigning the booted slot ID0 is a virtual-device choice; native consumers
identify the booted slot by manifest equality. No authentication result,
Apple key, or new commit hash is invented.

The isolated helper models get18, generate15 (host RNG, proposed state1),
delete19, and RL promotion1a. Unknown transitions receive no fabricated
success. The firmware commit-to-RL transition is still unimplemented.
Nonce generation refuses an occupied slot. Mutations are applied only after
successful output DMA, and response byte6 carries the slot state.

Default behavior is unchanged. Optional VMState serializes the slot contents,
but save/restore has not yet been validated; the experimental AES device still
blocks snapshots. Cold boot initializes from boot metadata; no external SEP
state store or firmware-update nonce persistence is claimed.

Two host tests pass: payload identity and lifecycle, plus strict frame parsing
and rejection. All75 existing host tests pass, and QEMU builds. APP_AES_XART1
is the fresh diagnostic validation run. It retains
native AES diagnostic waits and must not be treated as a performance control.

APP_AES_XART1 passed the exact failing path and ran to150.109 active seconds
without a panic. The native serial log positively reports
`current_slot_state=3, current_slot_id=0`, `booted slot id = 0`, and
`L boot detected.` The model supplies slot0 state3/56 bytes and slot1
state0/56 bytes; later AES transactions continue. This proves native hash
matching and cleanup, not simply absence of the previous assertion. The VM
was quit after capture. The new APP_AES_READY1 removes AES diagnostic waits
and repeats this path with no -ignore_dpa or -aes_spew flags.
