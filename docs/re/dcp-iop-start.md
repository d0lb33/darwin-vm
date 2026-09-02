# DCP IOP start — what must happen, and why it isn't happening

Source: iOS 27.0 beta (24A5430a), iPhone17,3 (iPhone 16, t8140/H17P),
kernelcache `firmware/bootkc`, kexts `com.apple.driver.RTBuddy`,
`com.apple.driver.AppleDCP`, `com.apple.driver.AppleMobileDispH17P-DCP`,
`com.apple.iokit.IOMobileGraphicsFamily-DCP` extracted to `/tmp/dvm/kexts/`,
device trees `/tmp/dvm/dtree_raw` (unpatched) and `/tmp/dvm/dt_dcp.bin`
(our `-enable dcp` tree). Extracted and disassembled 2026-09-01. Also
examines `/arm-io/ans/iop-ans-nub` and `/arm-io/smc/iop-smc-nub` from the
same unpatched tree as cross-checks (ANS enabled by the orchestrator in a
second experiment; SMC never enabled, read only from the DT for reference).

## Summary

`RTBuddy::start()` runs to completion and always returns `true` — it never
fails or panics — but the actual "load firmware into the IOP and release it
from reset" step is a *conditional* tail call buried three branches deep,
gated on a device-tree-derived firmware-availability signal that nothing in
our boot chain ever provides. This is true for **every** IOP that uses
`iop-nub,rtbuddy-v2` (DCP and ANS alike; the earlier `user-power-managed`
hypothesis is superseded — see below), so it is the single highest-value fix:
one working "firmware is ready" signal unblocks every RTKit coprocessor at
once, not just DCP. `AppleCLCD2` never fails either; it matches the `disp0`
node fine and its `start()` thread parks forever waiting for a DCP
handshake that the coprocessor, never having booted, can't deliver.

## The precondition chain in `RTBuddy::start()`

All addresses are from `com.apple.driver.RTBuddy` (arm64e, no symbol table,
chained fixups; `r2 -e bin.relocs.apply=true -e bin.cache=true -A`).
Function identity for `RTBuddy::start()` and `RTBuddy::_attemptFirmwareLoad()`
comes from embedded C++ signature strings (Apple's `require()`/`assert()`
macros bake `__PRETTY_FUNCTION__` into `__TEXT.__cstring`); every other
function name below is inferred from data flow and is only as certain as
stated.

```
RTBuddy::start(IOService *provider)              fcn.fffffff00a7bb72c
  string xref: "RTBuddy(%s): start(%p) - (%s@%s)\n" @ 0xfffffff007b21d69
  │
  ├─ 4 unconditional sub-init calls (property caches, PM setup,
  │  interrupt-controller registration — see "AIC cross-check" below)
  │
  ├─ if (dont-power-on-property || <vtable+0x6c0>(this))     // instance+0x109
  │     skip the rest entirely                                (RETURN, still returns true)
  │  else
  │     call RTBuddy::_maybeBootIOP-equivalent()               fcn.fffffff00a7bbefc
  │
  └─ mov w0, 1 ; retab                                          // start() always "succeeds"
```

Inside the boot attempt (`fcn.fffffff00a7bbefc`, called from `start()` above):

```
if (instance+0xd0)                    // "IOP start disabled" — see table below
    return;                            // not our case, see "Ruled out" table

fw_ready  = instance+0x2232            // (running-property present) || (no-firmware-service present)
preloaded = instance+0x2230            // pre-loaded-property present

if (!fw_ready) {
    // register a deferred callback for when firmware becomes available,
    // set instance+0x2198 = 1, and RETURN — no boot attempt at all
    fcn.fffffff00a7ea604(role, &fcn.fffffff00a7bc1b8 /* callback */, this, 0, 0);
    return;
}
if (preloaded) {
    <vtable+0x6a0>(this);              // direct boot for hardware with a preloaded firmware image
    return;
}
// fw_ready && !preloaded: boot only if a firmware object was actually delivered
if (fcn.fffffff00a7bc390(this))        // true only if instance+0x2170 != NULL
    RTBuddy::_attemptFirmwareLoad(this);  // fcn.fffffff00a7e38ec — tail call
// else: silently return. No log, no panic.
```

`fcn.fffffff00a7bc390` is the entire gate for the non-preloaded case:

```c
// fcn.fffffff00a7bc390(this)
result = NULL;
if (this->+0x2170 != NULL)               // cached "firmware asset" object
    result = this->+0x2170->vtable[0x540]();
if (result != NULL)
    return this->vtable[0x5e8]();         // presumed: actually kicks off the load
return false;
```

`this+0x2170` has **exactly one writer in the whole binary**
(`fcn.fffffff00a7bc318`, confirmed by exhaustive grep of every `str …,
[x, 0x2170]` and `str …, [x, #imm]` after an `add x,x,2,lsl 12` in the
disassembly), and it is only reachable from the deferred callback registered
in the `!fw_ready` branch above (`fcn.fffffff00a7bc1b8`/`0xa7bc1bc`, which
does `OSDynamicCast(matched_service, <externally-defined OSMetaClass, GOT
0xfffffff00837c0a90 — not resolved, see "Open questions">)` and, on a match,
stores the object at `+0x2170` and re-runs this whole check). **That callback
is never registered when `fw_ready` was already true**, which is exactly the
DCP case (see next section) — so for DCP, `+0x2170` is permanently `NULL`
and `_attemptFirmwareLoad()` is provably never called, with no log line and
no error, matching the observed silence exactly.

### Field-to-property map (all read in one property-parsing routine, `fcn.fffffff00a7c29b0`, reached by virtual dispatch — no direct caller found, consistent with `RTBuddy::init()`)

| Instance offset | Device tree property read | Evidence |
|---|---|---|
| `+0x106` | `power-managed` (bool present) | string @ `0xfffffff007b21129`, read at `0xa7c29e4` |
| `+0x107` | `user-power-managed` (bool present) | string @ `0xfffffff007b21137`, read at `0xa7c2a34`; panics at `0xa7c31dc` if both `+0x106` and `+0x107` are set, string "power-managed and user-power-managed are mutually exclusive device tree flags" @ `0xfffffff007b22cfa` |
| `+0x109` | `dont-power-on` OR `<vtable+0x6c0>(this)` | string @ `0xfffffff007b2114a`, read at `0xa7c2b1c`; combined at `0xa7c2b64-2b70` |
| `+0xcc`, `+0x158` | set to `3` if `quiesced` property present (value irrelevant, presence only) | string @ `0xfffffff007b22dc1`, read at `0xa7c2bf8-2c14` |
| `+0xd0` | `rtb_disable_fw_load` (boot-arg) OR (`disable-in-restore` property AND `-restore` boot-arg) OR `disable` property | separate function `fcn.fffffff00a7c2734`, strings "IOP start explicitly disabled by boot-arg"/"…disabled in restore"/"…disabled by device tree" @ `0xb282be/2f5/320`; property names `rtb_disable_fw_load`/`disable-in-restore`/`-restore`/`disable` @ `0xb22c63/c86/c99/ca2` |
| `+0x2230` | `pre-loaded` property present | string "pre-loaded" @ `0xfffffff007b22ee3`, read at `0xa7c2ea4-2ec4` |
| `+0x2231` | `running` property present | string "running" @ `0xfffffff007b22eee`, read at `0xa7c2ef4-2f14` |
| `+0x2232` | `+0x2231` OR `no-firmware-service` property present | string "no-firmware-service" @ `0xfffffff007b22ef6`, read at `0xa7c2f3c-2f54` |
| `+0x111` | `cold-boot-after-hibernate` property present | string @ `0xfffffff007b22f0a`, read at `0xa7c2f7c-2f90` |

### Ruled out: `+0xd0` (`iopStartDisabled`)

None of `rtb_disable_fw_load`/`-restore`/`disable-in-restore`/`disable` apply
to us: our boot-args are `rd=md0 serial=3 -v wdt=-1 wlan-olyhal-abort io=0x1f`
(no `-restore`, no `rtb_disable_fw_load`), and neither `iop-dcp-nub` nor
`iop-ans-nub` carries a `disable` or `disable-in-restore` property in the
unpatched tree (`/tmp/dvm/dtree_raw`, dumped with the repo's ADT decoder).
`+0xd0` is false for both, so this is not what stops us.

## LEAD 1 — what `user-power-managed` actually gates (and why it's not the blocker)

`power-managed` and `user-power-managed` are read at `+0x106`/`+0x107` and
are the two "who decides this IOP's power state" flags; the panic string at
`0xfffffff007b22cfa` proves they're meant to be mutually exclusive.
`user-power-managed` gates a **second, separate** check found in
`fcn.fffffff00a7bcbd4` (a different function from the boot path above,
called with `this` and a second argument): if `<vtable+0x6c0>(this)` is false
(the same predicate used at `+0x109` above), the function does its work
unconditionally; if true, it additionally requires a byte at `this+0x2244`
to be set before doing that work, and returns 0 (no-op) otherwise. No writer
to `+0x2244` was found anywhere in the binary via any addressing form,
meaning whatever sets it (presumably a public RTBuddy client API, e.g. an
explicit "please power me on" call from a joined power-management child) is
outside this kext or compiled in a way this static search didn't cover.

**This is not the DCP blocker.** The coordinator's second experiment shows
`/arm-io/ans/iop-ans-nub` has `power-managed = 1` (not `user-power-managed`)
and fails identically — zero mailbox access, same silence. Since the
`power-managed`/`user-power-managed` split can't explain two flag values
producing the same symptom, and since `/arm-io/smc/iop-smc-nub` (a coprocessor
that unquestionably boots on real hardware) *also* carries
`user-power-managed = 1` (see the table below), `user-power-managed` by
itself clearly does not prevent a real RTBuddy from booting its IOP. The
actual common blocker is the firmware-readiness gate above, which doesn't
consult `+0x106`/`+0x107` at all.

## Real-hardware reference: `/arm-io/smc/iop-smc-nub` proves the `pre-loaded` path

Read from `/tmp/dvm/dtree_raw` (unpatched, as-shipped device tree — this is
what iBoot hands XNU on a real device before any of our stripping):

```
AAPL,phandle = 0x76
compatible = iop-nub,rtbuddy-v2
firmware-name = "t8140smc"
pre-loaded = u32:0x0            <-- property PRESENT (value irrelevant; every
                                     other property in this table gates on
                                     getProperty()!=NULL, not on the value)
region-base = 0x30de00000
region-size = 0x1000000
user-power-managed = u32:0x1
quiesced = <NULL>
cold-boot-after-hibernate = <NULL>
...
```

This is the *only* node in the entire device tree — DCP and ANS included —
that carries `pre-loaded`, and it also carries `firmware-name` plus a
`region-base`/`region-size` pair describing where iBoot placed the firmware
image in DRAM. This is exactly the "direct boot" branch
(`<vtable+0x6a0>(this)`) in the flow above. Neither `/arm-io/dcp/iop-dcp-nub`
nor `/arm-io/ans/iop-ans-nub` has `pre-loaded`, `firmware-name`,
`region-base`, or `region-size` in the shipped DeviceTree image, which means
on **real hardware** these two properties (or the `+0x2170` firmware object)
must be added or delivered by something that runs before or during early
XNU boot and that we don't reproduce: either iBoot injects `pre-loaded` +
region info into the DCP/ANS nub after staging their firmware (most likely,
given the SMC precedent and that iBoot is documented to mutate several
device-tree properties at boot), or a kernel-resident firmware-loading
service (plausibly `AppleFirmwareKit`, which is present in our kernelcache
but whose delivery mechanism we haven't traced — see "Open questions")
publishes an object that satisfies the `OSDynamicCast` in the deferred
callback path. **Neither exists in our environment for any coprocessor.**

## Why DCP and ANS fail via *different* branches but with the *same* symptom

| | `iop-dcp-nub` | `iop-ans-nub` |
|---|---|---|
| `power-managed` | absent | **present** |
| `user-power-managed` | **present** | absent |
| `no-firmware-service` | **present** | absent |
| `running` | absent | absent |
| `pre-loaded` | absent | absent |
| `+0x2232` (fw_ready) computed | **true** (via `no-firmware-service`) | **false** |
| Branch taken in `fcn.fffffff00a7bbefc` | `fw_ready && !preloaded` → calls `fcn.fffffff00a7bc390`, which returns false because `+0x2170` was never populated (the only populator is gated on `!fw_ready`, which DCP never satisfies) | `!fw_ready` → registers a deferred "call me when firmware turns up" callback and returns immediately |
| Observed result | `RTBuddy(DCP): start()` logs, four client probes/matches happen, mailbox never touched | `RTBuddy(ANS2): start()` logs, four ANS client controllers probe/score (`AppleANS2CGv2Controller`/`AppleANS2NVMeController`/`AppleANS3CGv2Controller`/`AppleANS3NVMeController`, `/tmp/dvm/probe/ans2.serial.log:163-166`), mailbox never touched |

Both land at "no boot attempt", but for structurally different reasons — DCP
short-circuits into a dead branch (nothing will *ever* populate `+0x2170`
once `no-firmware-service` has made `fw_ready` true up front), while ANS
correctly defers and would boot **if** something ever satisfied its
notification. This fully explains the coordinator's observation that both
fail identically at the mailbox level while having opposite
`power-managed`/`user-power-managed` flags: neither flag is in the code path
that matters.

## AIC evidence cross-check (ties the RTKit code path to the coordinator's register trace)

`darwin_asc.c`'s documented interrupt order (`qemu-sptm/hw/arm/darwin_asc.c:176-184`):
index 0 = a2i (AP→IOP) not-empty, 1 = a2i empty, 2 = i2a (IOP→AP) not-empty,
3 = i2a empty. `/arm-io/dcp` interrupts = `0x2a8, 0x2a7, 0x2aa, 0x2a9`
(`docs/re/dcp-bringup.md`), so: `0x2a8`=a2i-not-empty (IOP-side),
`0x2a7`=a2i-empty (AP-side; **our model asserts this permanently**, since
`darwin_asc.c` line 181 drains a2i sends instantly — `qemu_set_irq(irq[1],1)`
unconditionally), `0x2aa`=i2a-not-empty (AP-side; this is the vector that
would fire when the IOP sends its first HELLO), `0x2a9`=i2a-empty (IOP-side).

`/tmp/dvm/aic17.log` shows, after the reset-time blanket mask, exactly three
individual (non-blanket) `MASK_SET` writes to AIC word 21 (which covers
vectors 672-703): bit 26 (`0x2ba`, the dart-dcp/dart-disp0 shared fault
vector), bit 10 (`0x2aa`), bit 7 (`0x2a7`) — and **zero** `MASK_CLR` writes
to that word, ever. This is exactly the two AP-relevant DCP-mailbox vectors
(`0x2a7`, `0x2aa`) plus the DART fault vector — evidence that RTBuddy's
unconditional sub-init calls (the four calls at the top of `start()`, before
the `+0x109` gate) *do* run and *do* register interrupt handlers for these
vectors (consistent with "registration happens early, unconditionally"),
but the final `enableInterrupt()`/unmask call is bundled with the boot
sequence that this document shows never runs. `/tmp/dvm/probe/autostart.stderr.log`
(`DARWIN_ASC_AUTOSTART`, forcing the modeled IOP to HELLO the AP anyway)
shows XNU never responds at all — consistent with vector `0x2aa`
(i2a-not-empty) staying masked, so the HELLO's interrupt is never delivered
to the AP CPU in the first place.

## LEAD 2 — why `AppleCLCD2` never registers

`com.apple.driver.AppleMobileDispH17P-DCP`'s `IOKitPersonalities`
(`/tmp/dvm/prelink/mobiledisp.json`, parsed from `__PRELINK_INFO`):

```json
"AppleADBE0-8970X": {
  "IOClass": "AppleCLCD2",
  "IONameMatch": ["disp0,t8140", "dispext0,t8140"],
  "IOProviderClass": "AppleARMIODevice"
}
```

A plain name-match personality, no extra `IOPropertyMatch`/score gate.
`dt_fixup.py`'s `EMULATED_FEATURES['dcp']` list includes `arm-io/disp0`, so
`compatible = "disp0,t8140"` survives into `/tmp/dvm/dt_dcp.bin`
unchanged — and indeed `disp0@0` registers as a device-tree nub in the
serial log (`Registering: .../AppleH17PPlatformIO/disp0@0`,
`/tmp/dvm/probe/dcpio.serial.log:245`). Matching is not the problem.

But `AppleCLCD2` (or `dispext0`'s equivalent) never appears as a
`Registering:` line anywhere in a 567-line, `io=0x1f`, 240-second boot log —
unlike every other successfully-started driver in the same log
(`RTBuddy(DCP)`, `RTBuddyService`, `AppleDCPExpert`, `AppleT8110DART`, etc.,
which all log `Registering: .../<ClassName>` the moment their `start()`
returns `true`). That means `AppleCLCD2::start()` has not returned — it is
still running, synchronously, inside the matching thread.

The last two log lines on this path, in order:
```
IOMFB: service matched: AppleDCPExpert
IOMFB AP: use_psd_dcp_power2: 0
```
`"IOMFB: service matched: %s"` lives in `com.apple.iokit.IOMobileGraphicsFamily-DCP`
(generic notification logger, format string only — the `%s` is substituted
at print time, we only observe the resulting text). `"IOMFB AP:
use_psd_dcp_power2: %d\n"` lives in `com.apple.driver.AppleMobileDispH17P-DCP`
at `0xfffffff007620ae3`, printed from `fcn.fffffff009188474` — the callback
that fires once `AppleDCPExpert` matches. Disassembly of that callback shows
it going on, right after this print, to invoke helpers whose failure paths
are labelled `"IOMFB: DCPPowerManager::start failed with 0x%x"`
(`0xfffffff007620ab3`) and `"IOMFB: DCPLink::start failed with 0x%x
(pipe:%u)"` (`0xfffffff007620b05`) — **neither of which printed**, meaning
`DCPPowerManager::start()`-equivalent *succeeded* and execution moved on
into further, unlogged setup that never returns.

Given the RTBuddy analysis above, this is exactly the expected outcome:
`AppleCLCD2`'s startup thread is parked somewhere inside `DCPPowerManager`/
`DCPLink` setup, waiting on a response over the DCP mailbox (AFK/EPIC
handshake or an RTKit power-state round-trip) from a coprocessor that never
finished booting. It doesn't fail, panic, or time out within our test
windows — it just never returns, so nothing downstream of it (the AFK/EPIC
service announcements documented in `docs/re/afk-epic-references.md`) ever
runs either. **LEAD 1 and LEAD 2 are the same root cause.**

## Ordered, ranked list of what's needed

1. **(Highest confidence, highest leverage) Give RTBuddy a firmware-ready
   signal for the DCP nub.** The cheapest lever is device-tree-only: add a
   `pre-loaded` property (any value, presence is all that's checked; modelled
   on `/arm-io/smc/iop-smc-nub`) to `/arm-io/dcp/iop-dcp-nub` — and, for
   parity, to `/arm-io/ans/iop-ans-nub` if ANS bring-up matters too. This
   routes `fcn.fffffff00a7bbefc` into the `<vtable+0x6a0>(this)` direct-boot
   branch instead of the dead `_attemptFirmwareLoad()` gate. **Untested
   consequence, flagged honestly:** we don't yet know what `<vtable+0x6a0>`
   does with a `region-base`/`region-size` we didn't provide, or whether it
   reads firmware bytes from a location our `darwin-unimp` catch-all would
   have to serve plausibly (a real firmware blob, or at minimum something
   that doesn't immediately fault/panic when parsed as one). This is the
   next concrete experiment, not a proven fix.
2. **(Medium confidence) Alternative: satisfy the deferred-firmware
   notification instead.** For nubs without `no-firmware-service` (ANS) or
   if the `pre-loaded` branch turns out to need real firmware bytes we can't
   supply, the other live path is publishing an object of the (currently
   unresolved) `OSMetaClass` type checked in `fcn.fffffff00a7bc1bc` so
   `RTBuddy`'s registered notification fires and populates `this+0x2170`.
   This is more invasive (would need `AppleFirmwareKit`'s real publish
   mechanism, or a `darwin_asc.c`-level shortcut) and depends on resolving
   the open question below first.
3. **(Confirmed not needed, but worth stating explicitly) `user-power-managed`
   requires no client-side power request from us to unblock the *initial*
   IOP boot** — that gate (`+0x2244` in `fcn.fffffff00a7bcbd4`) is on a
   different code path than the one that calls `_attemptFirmwareLoad()`.
   Don't spend effort building a fake power-management client; it won't
   move this forward.
4. **(No device-tree property found to restore)** Nothing was found that
   `dt_fixup.py` currently strips and should keep for this specific
   blocker — `iop-dcp-nub`'s properties (`no-firmware-service`, `quiesced`,
   `coredump-enable`, `watchdog-enable`, `cold-boot-after-hibernate`,
   `user-power-managed`) all survive already (`dt_fixup.py`'s
   `EMULATED_FEATURES['dcp']` list keeps `arm-io/dcp` and its children
   verbatim, and `del_compat()` only touches the `compatible` property, never
   deletes other properties or nodes). The fix here is *adding* `pre-loaded`
   (or equivalent), not restoring something removed.

## Open questions

- What `<vtable+0x6a0>(this)` (the `pre-loaded` direct-boot path) actually
  does — whether it reads `region-base`/`region-size` and DMAs from there,
  what it needs to see to avoid an early return of its own, and whether it's
  the function that finally writes `ASC_CPU_CONTROL` (offset `0x44`, per
  `darwin_asc.c`). Not traced; this is the next disassembly target once
  experiment 1 above is tried.
- The `OSMetaClass` checked in `fcn.fffffff00a7bc1bc`'s `OSDynamicCast`
  (GOT slot `0xfffffff00837c0a90` in `com.apple.driver.RTBuddy`) — the
  extracted single-kext Mach-O has no import/bind symbol table (`ii` returns
  0 entries even with `bin.relocs.apply=true`), because inter-kext
  references are pre-resolved to fixed kernelcache addresses at prelink time
  and this kext was extracted standalone. Resolving it needs either the
  whole merged kernelcache loaded in `r2` (so the fixed address can be
  looked up against `__PRELINK_INFO`/symbol data), or a live IOKit registry
  dump from a real device. Best guess, unconfirmed: an `AppleFirmwareKit`-
  published firmware asset/result object.
- The exact identity of the `<vtable+0x6c0>(this)` predicate gating `+0x109`
  (`dont-power-on`) and `+0x2244` (the `user-power-managed` "explicit
  request" flag in `fcn.fffffff00a7bcbd4`). Called with zero arguments in
  three independent, mutually consistent call sites; plausibly
  `IOService::isInactive()`-equivalent or an `RTBuddy`-local override, but
  not confirmed — the vtable itself couldn't be located without symbols (no
  `OSMetaClass`/vtable flags recovered by `r2`'s `avrr`, and raw-byte
  scanning for the PAC-signed slot value is not viable without the runtime
  signing key). Not on the critical path: `dont-power-on` is absent from our
  device tree, and `user-power-managed`'s gate is proven (by the SMC and ANS
  cross-checks) to not be the primary blocker regardless of this predicate's
  exact value.
- Whether `+0x2244`'s "explicit power request" gate (the `user-power-managed`
  behavior LEAD 1 originally asked about) matters *after* the firmware-load
  gate is fixed — i.e., once `_attemptFirmwareLoad()`/the preloaded path
  actually runs and the IOP is up, does some *later* power-state transition
  (sleep/wake, or the DCP's own runtime power votes) re-hit this same
  `+0x2244` requirement and need an explicit client? Not tested, since we
  never got past the firmware gate to observe it.
