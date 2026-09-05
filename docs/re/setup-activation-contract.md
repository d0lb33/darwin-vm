# Native Setup completion and activation contract (24A5430a)

## Scope and result

This note separates the three state machines which were conflated by the
Home-checkpoint debugger experiment:

```
Setup.app / Buddy completion  -> BYBuddyFinishedInitialRunKey -> no Setup flow
Lockdown / MobileActivation   -> ActivationState, BrickState    -> no “bricked” UI
LocalAuthentication / AKS     -> passcode + authenticated       -> unlock only
```

They are not substitutes for one another.  In particular, calling native
`-[SetupController markBuddyComplete]` completes Buddy but does not set the
Lockdown activation state.  The observed SpringBoard result after that call was
reason 2 because `-[SBLockdownManager brickedDevice]` remained true
([`setup-skip-runtime.md`](setup-skip-runtime.md):17-30).  Conversely, an
activation record is not a passcode or an authentication session.

The target is the iOS 27 / D47 cache used by the project (UUID
`58C54E82-C171-300E-AEEE-06DF937AA565`; `ipsw dyld info`), plus the extracted
24A5430a Setup executable.  All shared-cache VAs below are unslid and require
the current run's slide.

## What native completion persists

`-[SetupController markBuddyComplete]` begins at Setup static
`0x100013658`.  Its sequence at `0x100013bcc..0x100013be0` obtains
`NSUserDefaults` and stores `kCFBooleanTrue` under
`BYBuddyFinishedInitialRunKey`; it then calls the defaults synchronize path
(`0x100013bec..0x100013d6c`).  It also records `BYBuddyLastExitKey` at
`0x100013c24..0x100013c38`.  These are normal Setup-side completion writes,
not activation records.

That write has a demonstrated effect: after the native completion call,
`BYSetupAssistantNeedsToRun()` returned false in both bluetoothd and
SpringBoard ([`setup-skip-runtime.md`](setup-skip-runtime.md):17-29).  It is
the state that a native walk through Setup must create.  Do not replace it by
pre-seeding a preference; use the normal setup flow and verify the resulting
state with the read-only probes below.

### Starting a fresh native Buddy flow

The preferred input is a copied checkpoint from after migration/activation but
before the earlier debugger-driven `markBuddyComplete` call.  It needs no
Buddy reset and retains the native hactivation state independently.

For a disposable child that already contains completion state, the exact
SetupAssistant-owned launch reset is `_BYClearLaunchSentinel()` (static cache
VA `0x1cac230a0`, no arguments, Boolean return).  It calls
`sem_unlink("purplebuddy.sentinel")` at `0x1cac23110..0x1cac23118` and updates
only the `lastPrepareLaunchSentinel` diagnostic preference in
`com.apple.purplebuddy`.  `_LaunchSentinelExists()` is the complementary
native check at `0x1cac11eb4`: it calls `sem_open("purplebuddy.sentinel", 0)`;
the supported-device, non-internal-install path derives
`BYSetupAssistantNeedsToRun()` from that semaphore's absence.

The completion preference must also be cleared through CoreFoundation's
preference API before the new Setup process starts.  The authoritative reader,
`_BYSetupAssistantHasCompletedInitialRun` at `0x1cac22fe4`, reads
`SetupFinishedAllSteps` from `com.apple.purplebuddy` with
`CFPreferencesGetAppBooleanValue` (`0x1cac23070..0x1cac2308c`).  The matching
reset operation is therefore
`CFPreferencesSetAppValue(CFSTR("SetupFinishedAllSteps"), NULL,
CFSTR("com.apple.purplebuddy"))`, followed by
`CFPreferencesAppSynchronize(CFSTR("com.apple.purplebuddy"))`.  Clear
`SetupDone` through the same API as well: the internal Setup path reads that
key at `0x1cac227b4..0x1cac227c8`, and Setup's native completion writes the
corresponding `_BYBuddyDoneKey`.

These calls are a bounded reset of Buddy's own semaphore and preferences.  Run
them only in a disposable child, before SpringBoard/Setup has cached its
answer, then boot that child normally with no Setup, bricked-device, or
UI-authentication return override.  This is a procedure, not an executed
mutation in this investigation.

SpringBoard separately computes its setup reason in
`-[SBSetupManager updateInSetupMode]`, static `0x2246d37f0`:

* it selects reason 1 when `BYSetupAssistantNeedsToRun()` is true;
* it selects reason 2 when Buddy is complete but `brickedDevice` is true;
* the latter predicate is `lockdownState != 2` at
  `-[SBLockdownManager brickedDevice]` static
  `0x22458d184..0x22458d198`.

The reason-1 control flow and ordinary Setup launch sites are independently
mapped in [`setup-launch-runtime.md`](setup-launch-runtime.md):67-135.  The
reason-2 result is an activation/Lockdown observation, not a display,
persona, or Buddy-completion failure.

## Lockdown and MobileActivation contract

The local interfaces show that activation is daemon-owned and record-backed:

| Layer | Evidence | Meaning |
|---|---|---|
| Lockdown client | `liblockdown.dylib:_lockdown_copy_activationState` at `0x2c11e40f8..0x2c11e4168` calls `_lockdown_connect`, then `_send_get_value` with `kLockdownActivationStateKey`; `_lockdown_copy_brickState` at `0x2c11e416c..0x2c11e41dc` does the same for `kLockdownBrickStateKey`. | Consumers ask lockdownd for current state; they do not read a Buddy preference. |
| Lockdown key names | extracted `liblockdown.dylib` strings contain `ActivationState`, `ActivationRecord`, `BrickState`, `ActivationInfo`, `ActivationPrivateKey`, `WeHaveATicket`, and the `com.apple.PurpleBuddy` domain. | These are daemon values/domains.  They do **not** establish a safe on-disk plist location to edit. |
| MobileActivation public state | `MobileActivation` exports `_kMAActivationStateUnactivated` at `0x2c8346e30`, `_kMAActivationStateActivated` at `0x2c8346e38`, `_kMAActivationStateFactoryActivated` at `0x2c8346e40`, and `_kMAActivationStateUnavailable` at `0x2c8346e48`; it exports `_MAEGetActivationStateWithError` at `0x28f91ffd0` and `_MAEGetBrickState` at `0x28f92b02c`. | The supported read-side vocabulary includes Activated and FactoryActivated. |
| MobileActivation service route | The `mobileactivationd` closure imports `MobileActivation.framework/MobileActivation`, `libMobileGestalt.dylib`, `MobileKeyBag.framework/MobileKeyBag`, and `liblockdown.dylib`. | The daemon, SEP/keybag-adjacent state, and Lockdown are deliberately in the activation path. |

The record installation interface is not a local Boolean setter.  The same
framework exports `_MAEHandleActivationInfo` at `0x28f92b0ac`,
`_MAECopyActivationRecordWithError` at `0x28f91fff0`, and
`_MAEActivateDeviceWithError` at `0x28f92abb4`; its strings include
`InvalidActivationRecord`, `Invalid activation signature`,
`Failed to extract activation record`, `ActivationTicketSignature`, and
`ActivationRecord`.  A fabricated dictionary/file therefore has no evidence
of being accepted by the owning daemon and should not be used.

### Built-in local development activation: hactivation

The DeveloperOS daemon contains a concrete local activation route which was
not visible in the cache-extracted framework alone.  It is called
**hactivation** in `mobileactivationd`.

`_dealwith_activation` in the on-disk daemon starts at static
`0x1002f3ddc`.  At `0x1002f3ea4` it calls `_use_hactivation`
(`0x1002ec4ec`), which returns
`[[DeviceDetails sharedInstance] should_hactivate]`.  When that result is
nonzero, the daemon deliberately bypasses the activation-record validation
path at `0x1002f3f24` and logs:

```
Hactivation is enabled, short circuiting activation state to Activated.
```

It then makes these two native setter calls:

```
data_ark_set(dark, nil, kMAActivationStateKey,
             kMAActivationStateActivated, 0)  // 0x1002f3ed0..0x1002f3ee0
data_ark_set(dark, nil, kMABrickStateKey,
             kCFBooleanFalse, 0)              // 0x1002f3ef8..0x1002f3f04
```

This is the viable local virtualization seam.  It does not manufacture an
activation ticket or a signed activation record: the native daemon reports a
coherent activated, non-bricked developer device through its ordinary state
store and service.  `kMAActivationStateActivated` is the regular state,
rather than `FactoryActivated`, for this path.

The selector's input is the daemon-local `DeviceType` provider, rather than a
Setup or SpringBoard predicate.  `-[DeviceType should_hactivate]` is a one-byte
return from instance offset `+0x14` at static `0x1002ec368`; `_use_hactivation`
calls it at `0x1002ec514`.  The cache's
`-[DeviceTypeDeviceIdentity should_hactivate]` has the same one-byte layout at
static `0x22bc4d440`, but the daemon-local implementation is the authoritative
provider for this path.

There are three exact true-producing inputs in `mobileactivationd`'s own
`-[DeviceType init]` (static `0x1002eb57c`).  The normal development routes are
behind an internal-security-policy gate.  The initializer stores
`os_variant_allows_internal_security_policies(ProductType.UTF8String)` in
`DeviceType +0x15` at `0x1002eb5c..0x1002eb5dc`; if that byte is not one, the
`b.ne 0x1002ebdac` at `0x1002ebb00` skips `ShouldHactivate`, `HWModelStr DEV`,
and `allow-hactivation`.  Thus a production identity can read the property
correctly yet still leave `should_hactivate` clear.

For 24A5430a, libsystem's
`os_variant_allows_internal_security_policies` (static cache address
`0x22ffbb324`) has no positive file, preference, environment, boot-argument,
or DeviceTree override.  Its initializer (`0x22ffbae78`) derives the internal
state from the kernel comm-page word at `0xfffff00004084`: nonzero is state 3;
zero is state 2.  It also accepts a kernel-reported `kern.osbuildconfig` of
`development`, `debug`, `profile`, or `kasan`.  With the security-disable bit
clear, its exact result is `internal_state == 3 || (internal_state == 2 &&
development_kernel_state == 3)`.  PID 1 later caches the packed result through
`kern.osvariant_status` (`os_variant_init_4launchd`,
`0x22ffbcac4..0x22ffbcf8`).

The upstream kernel path supplies a narrow, coherent development-VM input:
`/chosen/debug-enabled` is copied as a u32 into `debug_enabled` in
`pexpert/arm/pe_init.c:471..488`; `PE_i_can_has_debugger()` returns it; and
`commpage.c:143..144` writes that result to `_COMM_PAGE_DEV_FIRM`.  A copied
DeviceTree with `chosen:debug-enabled=u32:1` therefore supplies the native
internal-policy prerequisite without changing the daemon.  It must be present
on the boot that initializes libsystem and `DeviceType`.

Once that prerequisite is true, the exact true-producing inputs are:

* It reads `IODeviceTree:/product/allow-hactivation` with
  `copyDeviceTreeInt:key:defaultValue:` at
  `0x1002eb900..0x1002eb940`.  When the returned `NSNumber.boolValue` is true,
  it writes `1` to the `should_hactivate` byte at
  `0x1002ebb9c..0x1002ebbd0`.  The exact development input is therefore
  `product:allow-hactivation = u32:1`.
* If `os_variant_allows_internal_security_policies()` was true and no prior
  input set the byte, it reads the MobileGestalt `ShouldHactivate` Boolean at
  `0x1002ebb04..0x1002ebb1c` and copies that result to `+0x14`.
* It propagates an `HWModelStr` suffix `DEV` to `+0x14` at
  `0x1002ebb8c..0x1002ebb98`; `lockdownd` has a corresponding `hw.model`
  `DEV` route at `0x10002b90c`.

The first input is the narrow local virtualization seam: it uses the daemon's
existing DeviceTree provider, has an explicit integer ABI, and does not depend
on a fabricated MobileGestalt cache answer or on a UI predicate.  The current
patched `firmware/dtree` has no `allow-hactivation` or
`enable-avp-fairplay` property under `/product` or `/chosen`, respectively.
A guarded test tree can therefore be produced from an already fixed-up tree
with the repository's raw-preserving tool:

```
python3 tools/dt_patch.py firmware/dtree /tmp/dvm/dt_hactivate.bin \
  -set product:allow-hactivation=u32:1
```

This is a copied-device-tree experiment artifact, not a state-file edit.  It
changes one native identity input and leaves Setup, SpringBoard, lockdownd, and
`mobileactivationd` unmodified.  If adopted after verification, the same
single property belongs in the normal DeviceTree construction path so every
fresh boot presents the same development identity.

The native opt-outs are equally concrete.  A boot argument exactly equal to
`disable-hactivation-ma=1` clears the byte at `0x1002ebbd8..0x1002ebbec`.
The initializer also clears it if `/AppleInternal/Lockdown/.hactivateoff`
exists (`0x1002ebbf4..0x1002ebc2c`), or if the
`com.apple.mobileactivationd` persistent domain supplies a true
`DisableHactivation` NSNumber (`0x1002ebc34..0x1002ebce0`).  The global
symbol loaded at static `0x1003cd820` is named `kMADisableHactivation`, but its
actual CFString bytes are `DisableHactivation` (18 UTF-8 bytes); the latter is
the persistent-domain dictionary key.  The mounted 24A5430a base System image
does not contain the marker.

Finally, after `_is_virtual_machine()` returns true, the provider reads
`IODeviceTree:/chosen/enable-avp-fairplay` at
`0x1002ebcfc..0x1002ebd64`; a true `NSNumber` clears `+0x14` at
`0x1002ebd70..0x1002ebd74`.  Thus the test tree must set
`product:allow-hactivation` to one and keep
`chosen:enable-avp-fairplay` absent or zero.

### Validated development-activation boot

`DEV_ACTIVATION_DIAG1` is the first no-LLDB validation of the complete native
input pair.  Its copied DeviceTree has both
`chosen:debug-enabled=u32:1` and `product:allow-hactivation=u32:1`; its boot
arguments retain no `disable-hactivation-ma=1`, FairPlay is absent, the marker
is absent, and the `DisableHactivation` preference is absent.  The signed
read-only activation probe recorded at 39.223 seconds of restored runtime:

```
DVM_ACTIVATION_PROBE mae.activation-state=Activated
DVM_ACTIVATION_PROBE mae.activation-error=<null>
DVM_ACTIVATION_PROBE mae.brick-state=0
```

The evidence is
`/tmp/dvm/checkpoints/WARM_DEV_EARLY_DIAG1/restores/DEV_ACTIVATION_DIAG1/serial.log:196-202`.
It validates `mobileactivationd`'s local hactivation setter path and the
MobileActivation client result.  The paired Lockdown query began at line 203
but had not returned in this capture, so this result does not yet claim a
Lockdown-consumer reply, SpringBoard reason 0, a completed Setup flow, or a
passcode/authentication result.

`lockdownd` independently obtains the MobileGestalt `ShouldHactivate` answer
at `0x10002b824..0x10002b830` and stores it as its `should_hactivate` field.
Its marker check at `0x10002860c` tests existence of
`/AppleInternal/Lockdown/.hactivateoff`; success clears the field at
`0x10002b95c`.  Capturing both the final `DeviceType` byte and the
`lockdownd` field on the first test run is still necessary: the DeviceTree
input proves the daemon's hactivation path, while the two processes retain
independent development-identity observations.

### Startup execution and trigger

No Setup action or activation request is needed to enter the hactivation
branch.  `mobileactivationd`'s `_main` starts at `0x10033aa58` and installs a
block whose invoke function is `___main_block_invoke_2` at `0x10033c034`.
That block calls `_performMigration` at `0x10033c048..0x10033c054`.  On the
successful migration path, after XPC-activity registration, `_performMigration`
calls `_dealwith_activation` at `0x1002f2af8..0x1002f2b04`, then records
migration completion.  `_handle_unbrick` also calls `_dealwith_activation` at
`0x1002fecfc`, but it is not needed for the ordinary daemon-start path.

The launchd service is `/usr/libexec/mobileactivationd`, with Mach services
`com.apple.mobileactivationd` and `com.apple.mobileactivationd.lockdown`.
The intended first trigger is therefore a normal daemon start after the
identity condition is present; it must reach the successful migration branch.
There is no reason to invoke the unbrick interface or a remote activation
client merely to populate the local hactivation state.

### DataArk ownership and state-change notifications

The hactivation writes are not ad-hoc plist edits.  `mobileactivationd` owns
a `DataArk` store and serializes writes through `_data_ark_set` at
`0x1002e163c`.  Its current store directory is constructed as the daemon's
system container plus `Library/internal`
(`_copy_data_ark_directory_path` at `0x100311d44..0x100311df4`).  Migration
code retains the legacy locations
`/private/var/mobile/Library/mad/data_ark.plist` and
`/private/var/mobile/Library/mad/activation_records`; these identify history,
not a second state source to seed.

The daemon registers the relevant updates before completing migration:

```
data_ark_register_set_notification(dark, nil, kMABrickStateKey,
    kNotificationBrickStateChanged, 8)        // 0x1002f22a4..0x1002f22d8
data_ark_register_set_notification(dark, nil, kMAActivationStateKey,
    kNotificationActivationStateChanged, 8)   // 0x1002f22e0..0x1002f2300
```

The imported notification constants resolve to the strings
`com.apple.mobile.lockdown.brick_state` and
`com.apple.mobile.lockdown.activation_state`.  On a changed value,
`_data_ark_set` persists the dictionary (`0x1002e1970..0x1002e1990`) and
posts the registered notifications (`0x1002e1994..0x1002e1a3c`).  Therefore a
device-detail adaptation must enter through hactivation/the daemon setter,
not by copying a cached DataArk file, so every subscribed native consumer
sees the same transition.

### Exact service replies and the Lockdown consumer

The daemon confirms both response ABIs:

* `-[MobileActivationDaemon getActivationStateWithCompletionBlock:]` at
  `0x100329b88` reads `kMAActivationStateKey` from DataArk, falls back to
  `kMAActivationStateUnactivated`, and calls its completion with
  `NSDictionary { kMAActivationStateKey: NSString }` and a nil error
  (`0x100329c00..0x100329cf4`).  The client-side `MadGate` reply block then
  extracts that NSString; see `MobileActivation` static `0x28f92353c`.
* `-[MobileActivationDaemon isDeviceBrickedWithCompletionBlock:]` at
  `0x10032d77c` reads `kMABrickStateKey`, converts it to an `NSNumber`, and
  calls its completion with `NSDictionary { kMABrickStateKey: NSNumber }`
  and a nil error (`0x10032d7f4..0x10032d904`).  The framework-side reply
  validator requires an `NSNumber` before returning the Boolean from
  `MAEGetBrickState()`.

`lockdownd` itself calls `MAEGetActivationStateWithError(NULL)` at
`0x10002a890..0x10002a898` and compares the returned string to both
`kMAActivationStateActivated` and `kMAActivationStateFactoryActivated`
(`0x10002a89c..0x10002a8e0`).  It is consequently on the same daemon/XPC
path as framework clients.  SpringBoard's final policy remains the observed
numeric Lockdown interface: `-[SBLockdownManager brickedDevice]` returns
`lockdownState != 2` at `0x22458d184..0x22458d198`.  A first hactivation run
should capture the actual `lockdownState` return to confirm that its
Activated result maps to `2` on this build; that mapping has not yet been
directly disassembled.

### Factory requests are unrelated

There is a factory request branch, but it is not a local emulator mode.  In
`_MAECreateActivationRequestWithError` at
`0x28f92acc0..0x28f92aea0`, a numeric `FactoryActivation` option takes
`_createFactoryActivationRequestFromMAD` at `0x28f920ebc`; otherwise it takes
the tunnel request builder.  The binary contains
`_kMAOptionsFactoryActivationRequest` (`0x2c8346ec0`),
`_FACTORY_ACTIVATION_TEST_URL` (`0x2cc0dd090`), and the production activation
endpoint string `https://albert.apple.com/deviceservices/deviceActivation`.
It also validates/handles activation information rather than manufacturing an
accepted response.  That route is not needed for the local hactivation path
and this investigation makes no service request.

No generic `DevelopmentActivation`, `FakeActivation`, or simulator/local-
activation option appears in the framework.  Hactivation is instead a
DeveloperOS daemon feature selected by `ShouldHactivate`.  Factory activation
is a distinct request/response protocol and is not part of this local path.

## Authentication, keybag, and persona are separate

In the live Home checkpoint, `SBFUserAuthenticationController` returned
`hasPasscodeSet = 1` at static `0x1bb281de8` and `isAuthenticated = 0` at
`0x1bb27afd8` ([`setup-skip-runtime.md`](setup-skip-runtime.md):92-105).
Forcing `isAuthenticated` true only allowed the display experiment to pass a
SpringBoard assertion; it did not submit a passcode or unlock SEP/keybag data.
That override must be absent from a normal warm-boot result.

### Keybag lock state and first-unlock state use different selectors

The observed passcode prompt is a keybag lock-state result, but the current
static evidence does **not** identify it with raw SKS DER field `ss`.  The two
consumer paths are separate and must stay coherent.

`-[SBFUserAuthenticationController hasPasscodeSet]` forwards to
`SBFMobileKeyBag` at `0x1bb281de8..0x1bb281dec`.  Its queued setter asks its
`SBFMobileKeyBagState` for `lockState` and caches:

```
_queue_hasPasscodeSet = (lockState != 3);  // 0x1bb293920..0x1bb29394c
```

The getter returns that byte through its queue block at
`0x1bb279fd0..0x1bb279fe0`.  `SBFMobileKeyBagState.lockState` reads the
`kSBFKeyBagInfoLockState` dictionary value (`0x1bb281d8c..0x1bb281dbc`) and
maps input values 0 through 7 through the table at `0x1bb3371f0`:

| input `ls` | SBF lock-state result |
|---:|---:|
| 0 | 0 |
| 1 | 2 |
| 2 | 1 |
| 3 | 3 |
| 4 | 4 |
| 5 | 5 |
| 6 | 6 |
| 7 | 7 |

Thus a no-passcode result requires the selector-17 lock-state field `ls` to
be 3; it is not a request to change the selector-7 `ss` field.

`_MKBGetDeviceLockState` at `0x1ae4da09c` zeroes a local output structure,
passes it to `__get_device_lock_state` at `0x1ae4da0f4`, and on success returns
only output word `+0x4` (`ldr w8,[sp,#4]` at `0x1ae4da0cc`).  The dynamic
provider resolved for this entry is `_aks_get_device_state`, whose public
AppleKeyStore wrapper is `0x23e341654` and explicitly sets user-client method
17 (`mov w1,#0x11` at `0x23e341658`) before entering `__get_device_state`.
There is no bitfield-to-enum conversion in this MKB return path.

`_MKBGetDeviceLockStateInfo` confirms the fields independently.  It places
the same device-state structure at `sp+0x40` and constructs a dictionary with
`state` from `+0x40` (`0x1ae4da640..0x1ae4da650`) and `ls` from `+0x44`
(`0x1ae4da62c..0x1ae4da63c`).  The short key strings are present at MobileKeyBag
file offsets `0x19288` (`"ls"`) and `0x19293` (`"state"`).  Therefore the
word returned by `_MKBGetDeviceLockState` is the selector-17 `ls` word, while
`state` is a distinct word at offset zero.

First-unlock uses a different path.  `_MKBDeviceUnlockedSinceBoot` at
`0x1ae4da524` obtains the selector-7 result through its own resolved provider,
then executes `ubfx w19,w8,#2,#1` at `0x1ae4da598..0x1ae4da59c`.  The kernel
selector-7 path exposes SKS record offset zero, currently decoded from the
model DER field `ss`.  The established response

```
31 12
  30 07 0c 02 62 68 02 01 fa    # bh = -6
  30 07 0c 02 73 73 02 01 04    # ss = 4
```

therefore makes `MKBDeviceUnlockedSinceBoot` true (`ss & 4`).  It does not,
by itself, establish the selector-17 `ls` value or its derivation.  The old
claim that `ss=3` was a no-passcode state was incorrect: changing it would
clear first-unlock and leaves the method-17 lock-state contract unaddressed.

The development target is consequently a coherent pair of observations:

```
aks_get_device_state / method 17:  ls == 3   # SBF maps this to no passcode
aks_get_lock_state   / method 7:   ss & 4    # first unlock remains true
```

The target is not a SpringBoard getter override, preference edit, or an
assumption that two fields share an encoding.  The AppleSEPKeyStore method-17
decoder identifies the missing member statically.  Its record encoder at
`0xfffffff009580f50` loads descriptor `0xfffffff00b821868` and record `+0`
(`0xfffffff009580f78..0x0f84`), then descriptor
`0xfffffff00b821870` and record `+4` (`0xfffffff009580f94..0x0fa0`).  The
paired parser at `0xfffffff009581144` resolves these same descriptors and
stores its first and second decoded integers at record `+0`
(`0xfffffff00958129c..0x12a4`) and `+4`
(`0xfffffff0095812a8..0x12b0`).  The DER descriptor literals in
`AppleSEPKeyStore` put UTF8 `"ss"` at file offset `0x59cc` and the immediately
following UTF8 `"sls"` at `0x59de`; existing selector-7 evidence identifies
the former descriptor (`0xfffffff00b821868`) as `ss`.  Therefore the
method-17 `+4` field is DER member `sls`, the source for MKB's `ls`.

A coherent development reply is the exact DER SET below.  Its DER length is
30 bytes, so the existing IPC-v1 payload builder must advertise length 30 and
pad the 38-byte header-plus-DER payload to 40 bytes:

```
31 1c
  30 07 0c 02 62 68 02 01 fa    # bh  = -6
  30 07 0c 02 73 73 02 01 04    # ss  = 4  (selector 7 first-unlock)
  30 08 0c 03 73 6c 73 02 01 03 # sls = 3  (selector 17 / MKB ls)
```

This static proof fixes the member spelling, destination offset, and wire
length.  A native boot must still establish that this complete response is
accepted in the current guest; it is not evidence of a durable credential or
keybag transition.  `SKS_CHANGE_LOCK_STATE` remains an all-zero no-op and
cannot currently be used as proof of such a transition.

A minimal diagnostic capture should log the final outputs, rather than infer
them from a UI label: record `MKBGetDeviceLockState` (the `ls` word),
`MKBGetDeviceLockStateInfo` (`state`, `ls`, `boff`, `fa`, and `countdown`), and
`MKBDeviceUnlockedSinceBoot` on the same boot.  A candidate implementation is
ready only when it shows `ls=3` and `ss bit 2=1` together.

Likewise, `usermanagerd`/personas are a prerequisite for SpringBoard process
operation, but the established persistent Data/User volume already creates the
needed personas ([`userspace-boot-state.md`](userspace-boot-state.md):235-245).
No examined Setup, Lockdown, or MobileActivation edge makes persona creation a
substitute for activation.

## Implementation and probe sequence

The minimally scoped implementation is an identity-only DeviceTree adaptation:
set `IODeviceTree:/chosen/debug-enabled = u32:1` and
`IODeviceTree:/product/allow-hactivation = u32:1`, while preserving an absent
or zero `IODeviceTree:/chosen/enable-avp-fairplay`.  The first property makes
the kernel publish development identity through DEV_FIRM; the second then
uses the daemon's ordinary hactivation selector.  Do not adapt SpringBoard
predicates, copy DataArk files, or manufacture activation records.
The native `mobileactivationd` hactivation branch remains responsible for both
state writes and notification fan-out.  Start with the guarded
`tools/dt_patch.py` artifact above; after it proves the normal daemon path,
move the same property into the normal DeviceTree construction path.

1. On the first boot after the DeviceTree adaptation, break at
   `mobileactivationd + 0x2ec368` (`-[DeviceType should_hactivate]`) and at
   `mobileactivationd + 0x2ec4ec` (`_use_hactivation`); record the Boolean at
   both sites.  A true value must lead to the two
   native setter sites `+0x2f3ed0` and `+0x2f3ef8`; capture their keys and
   values rather than writing a file externally.
2. Verify the normal service contract from independent consumers:
   `MAEGetActivationStateWithError(NULL)` returns `Activated`,
   `MAEGetBrickState()` returns false, and Lockdown reports its observed
   numeric state.  At SpringBoard, confirm `lockdownState == 2` and
   `brickedDevice == 0`.
3. Walk Setup.app normally.  Its native completion must still write
   `BYBuddyFinishedInitialRunKey`; that state is separate from hactivation.
   After completion, `BYSetupAssistantNeedsToRun() == 0` and
   `SBSetupManager` must select reason 0.
4. Test passcode/keybag state independently.  Before a no-passcode boot,
   confirm `MKBGetDeviceLockState == 3` and
   `MKBDeviceUnlockedSinceBoot == 1`; the latter requires keeping selector-7
   `ss=4` while selector-17 reports `sls=3`.  Confirm `SBFMobileKeyBag` and
   `SBFUserAuthenticationController hasPasscodeSet` both return zero, with no
   `isAuthenticated` predicate override.  A passcode-created result must
   instead reach Home through its normal unlock path.
5. Warm boot the resulting Data child without debugger Setup, bricked-device,
   or UI-auth return overrides.  The hactivation store and Buddy preference
   should each survive through their own native persistence path.

## Collected reproducible artifacts

`/tmp/dvm/SETUP_ACTIVATION1/` contains cache-extracted copies of the exact
`MobileActivation`, `liblockdown.dylib`, `mobileactivationd`, and the
one-property guarded DeviceTree artifact.  SHA-256:

```
fd0454dfff6f5929d1470ee5840417156ee8e9abd7849df39091bbf273a023df  MobileActivation
6c315791fae4e11f6784c40df4b36f98c27d0b644d8806e910a2011597e24226  liblockdown.dylib
8a1e67fd3708a6fa01d340380416b2faa2297471968dca0a5c52034269d402e6  mobileactivationd
b7132702187cd00368faf3cc260b51fb9079270717a91e9d72abf4bc7329baf3  dt_hactivate.bin
e4a08688506c9caafadbd180c72e7bb457cd5fb46cac025189bb4e4f8c20f2be  mkb/MobileKeyBag
948f744c2993a2ba1c03956756bb394e5f9e0c103ddd3ffa673331f3255e7202  aks/AppleKeyStore
260d8d39dd897216b02d435c1de12d79608c0e4c1fca14ce394f728d727a3c74  sbf/SpringBoardFoundation
```
