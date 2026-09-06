# Native battery through the SMC key endpoint (2026-09-05)

The iOS battery now comes from Apple's own driver chain running unmodified
against one new device model, the SMC coprocessor's key store:

```text
powerd (original, unpatched) <- IOPMPowerSource "AppleSmartBattery"
  <- AppleSmartBatteryManager (com.apple.driver.AppleSmartBatteryManagerEmbedded,
     IOProviderClass AppleSMC)
  <- AppleSmartBatteryPack / AppleChargerData / AppleSmartBatteryBank ...
  <- AppleSMC (com.apple.driver.AppleSMC) <- AppleSMCKeysEndpoint
  <- RTBuddy(SMC) endpoint 0x20 "SMCEndpoint1"  (RTKit over the ASC mailbox)
  <- qemu-sptm/hw/arm/darwin_smc.c  (/arm-io/smc, dt_fixup -enable smc)
```

No kernelcache patch, no powerd patch, no user-space publisher.  Branch
`claude/native-battery-emulation` (submodule branch `native-battery`).
Every claim below names the run that produced it; restore-probe logs are
`/tmp/dvm/probe/BATT_SMC*.{serial,stderr}.log`, the disk-boot candidate is
`/tmp/dvm/BATT_BP1/`.

## Why not the PMU, an SMC "desktop" battery, or a generic AppleSmartBattery

- `AppleARMPMUCharger` / `AppleARMPMUPowerSource` (AppleARMPMU.kext) match a
  `charger` nub under the SPMI PMU.  The T8140 tree has no such node (raw
  tree `/tmp/dvm/dtree_raw`, `/arm-io/nub-spmi0` children are `pmu-main`,
  `btm`, ...), so that chain cannot bind.  The RTC PMU model is untouched.
- The kernelcache has exactly two `IOPMPowerSource` subclasses
  (`ipsw kernel cpp --methods`, `/tmp/dvm/RTC_NATIVE1/cpp-methods.txt`):
  `AppleARMPMUPowerSource` and `AppleSmartBattery`.  The iPhone battery
  driver is `AppleSmartBatteryManagerEmbedded`, whose only provider is
  `AppleSMC` (kext prelink info).  The Mac `AppleSmartBatteryManager`
  (IOACPI/SMBus) is not in this kernelcache.
- The SMC itself is RTKit, not the x86 SMC MMIO/port interface: `/arm-io/smc`
  is `iop,ascwrap-v6`, role SMC; its nub `iop-smc-nub` carries
  `region-base 0x30de00000 / region-size 0x100000` and an RTBuddy endpoint
  service named `SMCEndpoint1` that `AppleSMCKeysEndpoint` matches.

## Bring-up gates (each one cost a boot)

1. **RTBuddy never booted the SMC.**  `RTBuddy(SMC): start` was logged but
   the mailbox saw no HELLO (probe BATT_SMC1).  The nub had `pre-loaded = 0`
   and no `no-firmware-service`, so RTBuddy waited on AFKFirmwareService,
   which this kernelcache cannot allocate.  `dt_fixup.py fixup_iops` now sets
   `pre-loaded = 1` and `no-firmware-service` on the SMC nub while leaving
   iBoot's real region alone (overwriting that region is the documented
   SPTM `VIOLATION_FRAME_TYPE` panic).  Host test:
   `tools/tests/test_dt_fixup_smc.py`.
2. **INITIALIZE must answer with a PA inside the nub region.**
   `AppleSMCKeysEndpoint` reads `region-base`/`region-size`
   (0xfffffff0095d3430, 0xfffffff0095d34a8), range-checks the reply
   (0xfffffff0095d34e0-0xfffffff0095d350c) and maps it with
   `IOMemoryDescriptor::withPhysicalAddress` (0xfffffff0095d351c).  The
   region is not DRAM on this machine; the model backs it with RAM and puts
   the shared buffer at +0x80000.
3. **Integer key data is big-endian.**  `#KEY` answered little-endian 3 gave
   "Number of keys on this target: 50331648" and "Key table cache not
   properly initialized" (BATT_SMC2); big-endian fixed it (BATT_SMC3:
   "Number of keys" correct, `NTAP` written, notifications enabled).
4. **A READ's size is the caller's buffer, not the key size.**  The battery
   pack reads every key with an 8-byte buffer.  `AppleSMCKeysEndpoint`'s
   polled reader only complains when the reply is *larger* than the request
   (`cmp w23, w20; b.hs` at 0xfffffff0095d43cc before "readKeyPolled
   sizeMismatch sent=%d rcvd=%d") and copies the reply's own size
   (0xfffffff0095d441c-0xfffffff0095d44bc).  Refusing 8-byte reads of 2-byte
   keys with 0x87 (BATT_SMC4) starved the pack; serving the key's size
   (BATT_SMC5) made every pack read succeed.
5. **`BPCC` is the battery-installed gate.**  `AppleSmartBattery` reads it as
   2 bytes right after probing its key table
   (0xfffffff0096a8154-0xfffffff0096a81c8): packs = `BPCC >> 8`, chargers =
   `BPCC & 0xff`, `BatteryInstalled = packs != 0`
   (0xfffffff0096a84a0-0xfffffff0096a84f4).  With the key absent the driver
   starts silently with no pack and `BatteryInstalled=false`, which is the
   state powerd's `control.internal` null dereference
   ([powerd-precise-battery-null.md](powerd-precise-battery-null.md)) comes
   from.  BATT_SMC4 (BPCC = 0x0101) is the first boot with
   `AppleSmartBatteryPack: ID: 0` alive.

## Protocol (RTKit endpoint 0x20)

m1n1 `proxyclient/m1n1/fw/smc.py` documents the M1 form; every field checked
against the T8140 endpoint agrees:

| | bits | meaning |
| --- | --- | --- |
| request | [7:0] type, [15:12] id, [23:16] size, [31:24] write size, [63:32] key (big-endian FourCC) | |
| reply | [7:0] result, [15:12] id, [31:16] size, [63:32] value (<= 4 bytes in-message, else shared buffer) | |
| types | 0x10 READ, 0x11 WRITE, 0x12 GET_KEY_BY_INDEX, 0x13 GET_KEY_INFO, 0x17 INITIALIZE, 0x18 NOTIFICATION, 0x20 RW_KEY | |
| results | 0x00 ok, 0x84 key not found, 0x85 not readable, 0x86 not writable, 0x87 size mismatch | |

GET_KEY_INFO writes `{size u8, type[4], flags u8}` to the shared buffer;
GET_KEY_BY_INDEX returns the key text as the value's little-endian bytes.
The reply id is checked against the outstanding command
("Tag/ID of response (0x%02x) doesn't match", 0xfffffff0095d30d8).

## How AppleSmartBattery consumes keys

The driver builds a 35-entry command table (templates at
0xfffffff007757b60, 16 bytes `{cmd, 11, type, 0}`) and assigns keys from the
20-row `{cmd, key}` table at 0xfffffff007758688 (builder loop
0xfffffff0096a70a4-0xfffffff0096a7124).  At start it issues GET_KEY_INFO for
each key (0xfffffff0096a7fac-0xfffffff0096a8140); a 0x84 renames the key
`NOOP` and later reads are skipped, so absent keys are tolerated.  Present
keys are read with the size GET_KEY_INFO reported, 1..32 bytes
(0xfffffff0096a7dcc-0xfffffff0096a7de4), widened to an integer, and either
published as a property (entries carrying an OSSymbol at +0x18,
0xfffffff0096b559c) or folded into IOPMPowerSource state in the poll routine
(0xfffffff0096b5b74-0xfffffff0096b6078):

| cmd | key | type | effect (evidence) |
| --- | --- | --- | --- |
| 0x09 | B0AV | int | voltage (per-object symbol); Linux macsmc-power: mV u16 |
| 0x0a | B0AC | int | `InstantAmperage` property, cached at +0x16c (0xfffffff0096b5c44-0xfffffff0096b5c94); s16 mA |
| 0x0b | B0IV | int | logged only (0xfffffff0096b5cdc) |
| 0x0f | B0UC | int | **IOPMPowerSource setCurrentCapacity** (0xfffffff0096b60b4, unless the user-client override flag at +0x152 is set) |
| 0x10 | B0CM | int | **IOPMPowerSource setMaxCapacity** (0xfffffff0096b60c4) |
| 0x23 / 0x76 | BMDA / BMSN | OSData | `ManufacturerData` / serial data |
| 0x100 | CHCE | bool | external-connected setter (0xfffffff0096b5bf0-0xfffffff0096b5c1c) |
| 0x200 / 0x400 | CHCC / BCF0 | bool | charge-capable / critical flags (0xfffffff0096b5d44-) |
| 0x300 | BSFC | bool | `FullyCharged` |
| 0x1700 | CHCR | bool | `AppleRawExternalConnected` |
| 0xe00 | CH0V | int | `AppleRawBatteryVoltage` |
| 0x1000 | CHAS | int | `ChargerConfiguration` |
| 0x1500 | B0BL | int | `BootVoltage` |
| 0x17 | B0CT | int | cycle count (per-object symbol) |
| 0x12 | B0TE | int | time to empty |
| 0x1600 | CHNC | int | no-charge reasons (0xfffffff0096b6020) |
| 0x120a | BCFW | bool | `SkipperNEIgnoreAtCritical` |
| 0x01 | CHSC | int | system charging |

The pack (`AppleSmartBatteryPack`) enumerates a much larger key list with
GET_KEY_INFO (about 350 keys in BATT_SMC4, `b??0`, `BL??`, `BR??`, `bl?0`
families) and reads the ones present: `BRSC`, `BUIC`, `B0RM`, `B0FC`, `B0DC`,
`B0CT`, `B0AV`, `B0AC`, `BMSN`, `BMDT`, `BSFC` in BATT_SMC5.  Its shutdown
record check reads `UQd0` (2 bytes, falls back to `UPOF`) and clears with a
1-byte write to `UB0C` (0xfffffff0096998e8-0xfffffff009699984).

Key meanings and widths are taken from the same keys in Linux's upstream
`drivers/power/supply/macsmc-power.c` (Apple silicon SMC firmware shares the
namespace): B0AV mV, B0AC mA signed, B0CT cycles, B0TE/B0TF minutes
(0xffff unknown), B0RM/B0FC/B0DC mAh, B0AT K*10, BUIC percent, CHCE/CHCC/
CHSC/BSFC flags, CHNC bit 0 = battery full, BCF0 u8 critical flags, BMSN/
BMDN/BMDT strings, BNCB cells.  Keys with no reference and no telling
property name are left absent (0x84).

`batman=<mask>` (PE_parse_boot_argn at 0xfffffff0096bcdc4, string
0xfffffff00775601a) enables the kext's os_log diagnostics; they go to the
unified log, not the serial console.

## The model

`darwin_smc.c` keeps a small key table: the AppleSMC core keys (`CLKH`,
`WKTP`, `NTAP`, `RGEN`, `aDC#`, `LG??`, `MBS?`, `MESS`), `BPCC`, the
command-table keys above, the macsmc-power pack keys, and the shutdown pair.
A `SMCBattery {soc, external, charging}` state drives every value through
`smc_battery_apply()`; default 80 %, charger attached, charging.
`DARWIN_SMC_BATTERY=soc,ext,charging` overrides the initial state.
Unknown keys are logged once per access and refused with 0x84; a
`GET_KEY_INFO` storm from the pack is the expected first-boot noise.
`DARWIN_SMC_DEBUG=1` logs every message.

## Runs

| run | kind | result |
| --- | --- | --- |
| BATT_SMC1 | restore probe | RTBuddy(SMC) start, no HELLO (nub not pre-loaded) |
| BATT_SMC2 | restore probe | HELLO, INITIALIZE, key cache init failed (LE #KEY) |
| BATT_SMC3 | restore probe | key cache ok, NTAP written, AppleSMCPMU/Charger/PowerOut start; 47 keys unknown |
| BATT_SMC4 | restore probe | BPCC present: AppleSmartBatteryPack ID 0 created, reads refused on size |
| BATT_SMC5/6 | restore probe | every pack read served; command-table keys polled repeatedly |
| BATT_BP1 | profile disk boots | storage pass, no frame in 240 s (identical to the plain patched profile run BP_PATCHED3 on the same seeded parent; not battery-related) |
| BATT_NB1 | guarded install | original powerd restored, publisher removed, on top of the working CLOCK_SOFTWARE_INSTALL1 disk |
| BATT_SYS1 | disk boot 480 s | original powerd publishes `InternalBattery-0`, Is Present 1, AC Power, charging; Current Capacity 1 / Max 0 (B0UC/B0CM absent); lock screen drawn; checkpoint BATT_NATIVE_LOCKSCREEN1 |
| BATT_SYS2/3 | disk boot, probe | AppleSmartBatteryPack BatteryData 80 % / 2720 / 3400 / 4120 mV; power source CurrentCapacity 0, MaxCapacity 0 |
| BATT_SYS4 | disk boot, probe | with B0UC/B0CM: IOPS Current Capacity 80, Max Capacity 100; registry CurrentCapacity 80, MaxCapacity 100, IsCharging 1 |
| BATT_SYS5 | disk boot 600 s | runtime change through `qom-set /machine/smc-battery`; see "Runtime changes" |

## What the power source publishes (BATT_SYS4, `power-rtc-probe` registry dump)

Top level of `AppleSmartBattery`: BatteryInstalled 1, CurrentCapacity 80,
MaxCapacity 100, IsCharging 1, ExternalConnected 1, ExternalChargeCapable 1,
FullyCharged 0, Voltage 4120, InstantAmperage 480, CycleCount 12,
BootVoltage 4120, AppleRawBatteryVoltage 5000, AppleRawExternalConnected 1,
AvgTimeToEmpty 65535, TimeRemaining 0, Serial DVMBATT000000001, plus the
pack's BatteryData {CurrentCapacity 80, DesignCapacity 3561,
FullChargeCapacity 3400, RemainingCapacity 2720, FullyCharged 0}.
powerd's IOPS description of it: Is Present 1, Current Capacity 80, Max
Capacity 100, Is Charging 1, Power Source State "AC Power", Raw External
Connected 1, Time to Full Charge -1.

The pack derives its BatteryData from BRSC (StateOfCharge), BUIC
(CurrentCapacity), B0RM (AppleRawCurrentCapacity), B0FC
(AppleRawMaxCapacity), B0DC (DesignCapacity), B0CT, B0AV, B0AC, BSFC, BMSN
and BMDT (read as a little-endian integer: 859058229 for the bytes "3405",
so BMDT is numeric on this build and still needs a real encoding).

## Notifications and runtime changes

AppleSMC turns a type-0x18 endpoint message into a category from byte 7
(0x70 System State, 0x71 Power State, 0x72 HID Event, 0x73 Battery Auth,
0x74 GG Firmware Update, 0x76 Thermal Event; labels
0xfffffff007728510-0xfffffff007728558, switch 0xfffffff0095c9eec-
0xfffffff0095c9fdc) and three argument bytes 6, 5, 4 published to the
callbacks registered for that category (0xfffffff0095ca244-
0xfffffff0095ca2f4).  AppleSmartBattery's callback re-polls the battery for
subtypes 1, 3, 6 and 0xb (0xfffffff0096b84f4-0xfffffff0096b8550).

The model exposes `/machine/smc-battery` (QOM object, properties `soc`,
`external`, `charging`).  `tools/qmp.py <run>/qmp.sock qom-set
path=/machine/smc-battery property=external value=false` rewrites the keys
and raises a Power State notification with subtype 1.

## Candidate

`tools/rootfs/bootstrap_profile.py --profile patched-native-battery` keeps
the patched profile's display allocation, Settings scale, clock fallback,
input helper and six-CPU adapter, but installs the **original powerd** and
**no `power-pv-service`**; the SMC model is the only internal power source.
Host test: `tools/tests/test_bootstrap_profile.py`.

Because the profile pipeline's fresh-seeded parent does not draw a frame
within its boot bound for the plain patched profile either (BP_PATCHED3),
the validated candidate was built on the working
`CLOCK_SOFTWARE_INSTALL1` chain instead with
`tools/rootfs/prepare_native_battery_candidate.py`: a guarded
restore-ramdisk installer that checks the guarded powerd and the launchd
cache preimages, restores `powerd.original`, rewrites the cache without the
`com.apple.dvm-power-pv-service` job and removes the job's plist and
binary (`/tmp/dvm/BATT_NB1`, marker `DVM_NATIVE_BATTERY_INSTALLED`).

Launch configuration (manifest `/tmp/dvm/BATT1/warm-manifest-nb5.json`,
run with `tools/warm_boot_probe.py`):

- QEMU from this worktree (`darwin_smc.c` present), `-smp 6`,
  `DARWIN_SMP_PV=1` with the SMP kernelcache `DISPLAY_SMP6.bootkc`;
- device tree `dt_fixup.py /tmp/dvm/dtree_raw ... -enable ans -enable smc
  -enable sep -enable dcp -enable spmi -dram 12G -development-activation`;
- trust cache `CLOCK_SOFTWARE_PATCH1/system.tc` (the original powerd is a
  stock binary already in it) plus the probe's CDHash;
- `DARWIN_RTC_PV=0`, the display environment of the patched profile, no
  `DARWIN_SMC_*` variable (defaults: 80 %, charger attached, charging).
