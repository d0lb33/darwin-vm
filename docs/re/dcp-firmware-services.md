# DCP firmware EPIC service surface

Source: iOS 27.0 beta (24A5430a), iPhone17,3 (iPhone 16, t8140/H17P). Firmware
`t8140dcp.im4p` (`/tmp/dvm/fw/24A5430a__iPhone17,3/t8140dcp.im4p`, IM4P type
`dcpf`, LZFSE payload 2,565,778 bytes, confirmed via `ipsw img4 im4p info`),
decompressed to the raw 16,695,296-byte bundle `/tmp/dvm/dcpfw`. Cross-checked
against the AP-side kexts `com.apple.driver.DCPAVFamilyProxy`,
`com.apple.driver.DCPDPFamilyProxy`, `com.apple.driver.AppleDCPDPTXProxy`,
`com.apple.iokit.IOMIPIFamily`, `com.apple.driver.AppleDCP`,
`com.apple.driver.AppleMobileDispH17P-DCP` and the kernelcache
`__PRELINK_INFO` at `/tmp/dvm/prelink/info.plist`. Extracted 2026-09-01.

All firmware citations are `dcpfw+0xOFFSET`, a **file offset into the
16.6 MB decompressed bundle**, found by exhaustive string/byte scanning
(see "Bundle layout" for why this project gave up on resolving these offsets
to firmware virtual addresses). AP-side citations are the kext bundle
identifier and the `IOKitPersonalities` key, read directly out of the
kernelcache's own prelink plist with `plistlib` — no disassembly needed for
those, they are literal property-list entries.

## Summary

The DCP firmware is a custom multi-image "BUND" container, not a bare
Mach-O; inside it are (at least) two full RTKit task images that split into
an "untrusted"-looking client-side task and a smaller "trusted"-looking
server-side task, connected by an internal RPC layer Apple calls
**Tightbeam** (`tb_message.c`, `tb_connection.c`, `tb_codec.c`, `dcpfw+0x497080`
region) — a different, previously-undocumented-in-this-repo mechanism from
the AP-facing AFK/EPIC transport. The AP-facing surface itself is generic
AFK/EPIC exactly as `docs/re/afk-epic-references.md` describes from the
M1-era Linux driver, confirmed directly in this firmware's own class name and
log strings (`AFKEPInterfaceV2`, `pktCat`/`pktType`/`commandID`, `sendOpenReport`
/`sendPublishReport`/`sendCloseReport`/`sendTerminate`). Combining the
firmware's own `-epic` name strings with the kernelcache's `IOKitPersonalities`
gives a **definitive, kext-sourced list of 16 `EPICName → IOClass` pairs**,
plus roughly a dozen more `-epic` names the firmware announces that have no
matching AP-side `IOPropertyMatch` on `EPICName` found in this kernelcache — a
useful signal for what to skip in a minimal model.

## Bundle layout

`dcpfw` opens with a `DNUB` ("BUND" reversed) container header at file offset
`0x8`. A table of 11 named pools follows at offset `0x220`-ish, each record
24 bytes: `u32 id`, `char tag[4]` (ASCII, not reversed — `"txtk"` etc. appear
as literal bytes), `u64 offset`, `u64 size`. Read with a small Python script
(no existing decoder in this repo covers this format):

| tag | id | file offset | size | contents |
|---|---|---|---|---|
| `txtk` | 0xa | 0x010000 | 0x022000 | kernel-privileged boot stub, a complete standalone `MH_EXECUTE` arm64e Mach-O (`__TEXT` vmaddr `0x08000000`, `LC_UNIXTHREAD` entry, no symtab) |
| `txtr` | 0xa | 0x032000 | 0x586000 | **all `__TEXT`-segment bytes** (code+rodata+cstrings) for the two big RTKit task images, back to back |
| `txtu` | 0xe | 0x5b8000 | 0x008000 | small "user text" pool, not investigated |
| `tadk` | 0x11 | 0x5c0000 | 0x004000 | `txtk`'s `__DATA` |
| `tadr` | 0x11 | 0x5c4000 | 0x9d0000 | **all `__DATA`+-segment bytes** (data, `__OS_LOG` strings, `__DATA_CONST`, appconfig) for the same two task images |
| `tadu` | 0x15 | 0xf94000 | 0 | empty on this build |
| `orsd` | 0x15 | 0xf94000 | 0x008000 | "ro shared data" |
| `wrsd` | 0x15 | 0xf9c000 | 0 | empty on this build |
| `dlon` | 0x114 | 0xf9c000 | 0x028000 | **Mach-O headers only** — see below |
| `ldbu` | 0x15 | 0xfc4000 | 0x028000 | not investigated |
| `glso` | 0x104 | 0 | 0 | empty on this build (name suggests "global oslog", unused here) |

Evidence: table entries read at `dcpfw+0x284` (`txtk`) through `dcpfw+0x374`
(`glso`); every pool's `offset+size` equals the next pool's `offset` exactly,
confirming the table is self-consistent.

**`dlon` holds three complete Mach-O header+load-command blobs, tightly
packed** (each one's header+load-commands ends exactly where the next one's
magic begins — `dcpfw+0xf9c000`, `+0xf9c608`, `+0xf9d3c0`, verified
`0xf9c000+32+0x5e8(sizeofcmds)==0xf9c608` and
`0xf9c608+32+0xd98==0xf9d3c0` exactly). None of the three has any segment
content physically adjacent to its own header — their declared segment
`fileoff` values, taken literally, run for megabytes past the 0x28000-byte
`dlon` pool. **The actual segment content lives in `txtr`/`tadr`**, addressed
by `vmaddr − bias`, where `bias` is fixed per image and recovered
empirically: `txtr`'s first byte (`dcpfw+0x32000`) disassembles as a valid
ARM64 function prologue (`adrp`/`add`/`bl`, a stack-guard check) exactly when
treated as vmaddr `0x04000000` of the larger `dlon` image
(`bias = 0x04000000 − 0x32000 = 0x3FCE000`); the same offset computed for a
known `__DATA` symbol (`_rtk_patchbay`, vmaddr `0x044a4000`) against `tadr`'s
base with a **separately-fitted** data bias (`0x3EE0000`) lands on plausible
patch-table bytes (`"GKTS"`/`"grAb"` magic at `dcpfw+0x5c4000`). **`__TEXT`
and `__DATA` are stored in file-offset order that does not preserve their
virtual-address adjacency** — i.e. this is a Harvard-style repack, not a
flat Mach-O, and each image needs its own two biases (text, data) fitted
independently. This project did not attempt to derive `LC_SYMTAB`'s
`symoff`/`stroff` (`0x9b3000`/`0xb84850` for the big image) into a working
file offset — a `strings`/regex scan for `_Z`-mangled C++ symbols anywhere in
`dcpfw` found **zero** hits, so the shipped bundle most likely has its symbol
table stripped and these fields are vestigial. All findings below therefore
come from **string mining**, exactly as the task brief prescribed, not from
symbolized disassembly.

The two big Mach-O images inside `dlon`:

| # | header @ | filetype | `__TEXT` vmaddr/size | `__DATA...` vmaddr range | `nsyms` (vestigial) | role (inferred, see below) |
|---|---|---|---|---|---|---|
| `dlon#1` | `0xf9c000` | `MH_PRELOAD` | `0xffffffff00000000` / `0x10000` | — | 0 | tiny; sections `__head`/`__tunables`/`__kernel_stack` — looks like the earliest-boot monitor/reset code, not investigated further |
| `dlon#2` | `0xf9c608` | `MH_PRELOAD` | `0x04000000` / `0x4a4000` | `0x044a4000`–`0x04a52000` | 119173 | **larger task** — hosts almost every `*EPClient`/`*Proxy`-facing "Client" class (`DCPExpertSecureInterfaceClient.cpp` @ `0x497d01`, `DCPTrustedInterfaceClient.cpp` @ `0x497d99`, `DCPUntrustedMIPIControllerEPClient.cpp` @ `0x4aa08f`) — i.e. the side that *calls into* the trusted domain |
| `dlon#3` | `0xf9d3c0` | `MH_PRELOAD` | `0x04000000` / `0xe0000` | `0x040e0000`–`0x0452c000` | 38090 | **smaller task** — hosts the "Server" counterparts (`DCPExpertSecureInterfaceServer.cpp` @ `0x5aa516`, `DCPTrustedInterfaceServer.cpp` @ `0x5aa6ac`) and, critically, `AppleDCPSynopsysMIPIDSIControllerTrusted.cpp` @ `0x5b4251` — the actual MIPI-DSI hardware driver lives here |

Both images embed **their own separate copy** of a shared platform-config
string table (every `dcpav-*-epic` name below appears twice, once in each
image's `__TEXT,__cstring`, at a fixed offset apart — e.g.
`dcpav-controller-epic` at both `0x3ea572` and `0x59abda`). This, plus the
client/server split above, is the strongest evidence for a **trusted/untrusted
security domain split inside the DCP firmware itself**: `dlon#2` is the
general-purpose "untrusted" RTKit OS that hosts AFK/EPIC and most driver
logic, `dlon#3` is a small "trusted" task holding the panel-identity/HDCP/DSI
-critical code, and the two talk to each other over Tightbeam, not AFK. This
is inferred from string placement, not from disassembling the IPC — flagged
as an interpretation, not a certainty.

## The AP-facing AFK/EPIC transport, confirmed in this firmware

The firmware's own class is `AFKEPInterfaceV2` (`dcpfw+0x497fef`,
`AFKEPInterfaceV2.cpp`), with log strings that map directly onto
`docs/re/afk-epic-references.md`'s framing model:

```
fc4284  [AFK][AFKEPInterfaceV2:%p]handleMessage (pktLen %zu pktCat 0x%x pktType 0x%x internal:%i)
fc4391  [AFK][AFKEPInterfaceV2:%p] Enqueue Command (packetType 0x%x commandID 0x%x) state %#x
fc446a  [AFK][AFKEPInterfaceV2:%p]Enqueue Response (pktType 0x%x commandID 0x%x result 0x%x) state %#x
fc44c9  [AFK][AFKEPInterfaceV2] Send error Response (pktType 0x%x, commandID 0x%x)
fc42de  [AFK][AFKEPInterfaceV2] sendOpenReport:0x%x
fc430a  [AFK][AFKEPInterfaceV2] sendPublishReport:0x%x
fc4339  [AFK][AFKEPInterfaceV2] sendTerminate:0x%x
fc4364  [AFK][AFKEPInterfaceV2] sendCloseReport:0x%x
```

`pktCat`/`pktType` are this firmware's names for the AFK doc's
`epic_sub_hdr.category`/`.type`; `commandID` is the doc's `epic_cmd`
group/command RPC tag. `sendPublishReport` is almost certainly the firmware's
own name for the `REPORT`/`ANNOUNCE` (`0x30`) frame the AFK doc documents;
`sendOpenReport`/`sendCloseReport` are **not** named in either of the AFK
doc's sources (Linux only has `ANNOUNCE`/`TEARDOWN`/`STD_SERVICE`) — plausibly
an additional pair of report subtypes this firmware generation added. Not
disassembled to get numeric subtype values; flagged as an open question
below.

The ring transport itself is named `iop_ringbuffer.h` (`dcpfw+0x497f01`),
consistent with the AFK doc's `QE_MAGIC` = `"IOP "`. Generic AFK object
classes present and matching the doc's model one-for-one: `AFKDictionary`,
`AFKArray`, `AFKNumber`, `AFKString`, `AFKSerialize`, `AFKObject`,
`AFKNotification`, `AFKMemoryDescriptor`/`AFKBufferMemoryDescriptor`,
`AFKMailboxEventSource`, `AFKEventSource`, `AFKWorkloop` — all as `.cpp` file
strings in the `0x4b4xxx`-`0x4b5xxx` region.

`IOMobileFramebuffer_RemoteCalls.cpp` (`dcpfw+0x4c0dc3`) itself logs
`"IOMFB: failed to serialize AFKArray"` (`dcpfw+0x4c0d9e`) — i.e. **some**
IOMFB-adjacent traffic (at minimum the `IOMFBAOPDisplayManagerEPICClient`
path to the AOP coprocessor, see below) is carried as AFK-serialized objects,
even though the core endpoint `0x37` link itself is not AFK-framed (per
`docs/re/afk-epic-references.md`'s exclusion, which this investigation did
not overturn — see "Endpoint 0x37" below).

## EPIC service table

### Confirmed both sides: firmware announces the name, a real kext matches on it

This is the authoritative list: every row is a literal `EPICName` string
found **both** in `dcpfw`'s `-epic`-suffixed string set and as an
`IOPropertyMatch = {"EPICName": ...}` key inside a real `IOKitPersonalities`
entry in the kernelcache `__PRELINK_INFO` — i.e. XNU really will try to bind
this class if our model announces this name.

| EPICName | firmware evidence | AP-side IOClass | kext bundle |
|---|---|---|---|
| `dcpav-controller-epic` | `dcpfw+0x3ea572`, `+0x59abda` | `DCPAVControllerProxy` | `com.apple.driver.DCPAVFamilyProxy` |
| `dcpav-device-epic` | `dcpfw+0x3ea5b9`, `+0x59ac2a` | `DCPAVDeviceProxy` | `com.apple.driver.DCPAVFamilyProxy` |
| `dcpav-service-epic` | `dcpfw+0x3ea7ae`, `+0x59ae2f` | `DCPAVServiceProxy` | `com.apple.driver.DCPAVFamilyProxy` |
| `dcpav-video-interface-epic` | `dcpfw+0x3f6f61`, `+0x59bb21` | `DCPAVVideoInterfaceProxy` | `com.apple.driver.DCPAVFamilyProxy` |
| `dcpav-power-epic` | `dcpfw+0x3ebfee`, `+0x59c79b` | `DCPAVPowerControllerProxy` | `com.apple.driver.DCPAVFamilyProxy` |
| `dcpav-audio-interface-epic` | `dcpfw+0x3eb492`, `+0x59bbbd` | `DCPAVAudioInterfaceProxy` | `com.apple.driver.DCPAVFamilyProxy` |
| `dcpav-cec-interface-epic` | `dcpfw+0x3eb4be` | `DCPAVCECInterfaceProxy` | `com.apple.driver.DCPAVFamilyProxy` |
| `dcpav-sac-epic` | `dcpfw+0x3ebfdf`, `+0x59c77f` | `DCPAVRemoteSACControllerProxy` | `com.apple.driver.DCPAVFamilyProxy` |
| `dcpdp-controller-epic` | `dcpfw+0x3f7a98` | `DCPDPControllerProxy` | `com.apple.driver.DCPDPFamilyProxy` |
| `dcpdp-device-epic` | `dcpfw+0x3f7b54` | `DCPDPDeviceProxy` | `com.apple.driver.DCPDPFamilyProxy` |
| `dcpdp-service-epic` | `dcpfw+0x3f7eb9` | `DCPDPServiceProxy` | `com.apple.driver.DCPDPFamilyProxy` |
| `dcpdptx-port-epic` | `dcpfw+0x3e9669` | `AppleDCPDPTXRemotePortProxy` | `com.apple.driver.AppleDCPDPTXProxy` |
| `dcp-lpdptx-port-epic` | `dcpfw+0x3e95dc` | `AppleDCPLPDPTXPortProxy` | `com.apple.driver.AppleDCPDPTXProxy` |
| `dcpdptx-hdcp-interface` | `dcpfw+0x3e99d0` | `AppleDCPDPTXRemoteHDCPInterfaceProxy` | `com.apple.driver.AppleDCPDPTXProxy` |
| `dcpdptx-hdcp-auth-session` | `dcpfw+0x3e9b42` | `AppleDCPDPTXRemoteHDCPAuthSessionProxy` | `com.apple.driver.AppleDCPDPTXProxy` |
| `dcpmipi-controller-epic` | `dcpfw+0x3f707f`, `+0x5a819f` | `DCPMIPIControllerProxy` | `com.apple.iokit.IOMIPIFamily` |

`dcpmipi-controller-epic` was **not** in the task's candidate list and has no
counterpart in either of `docs/re/afk-epic-references.md`'s M1-era sources —
this is the one new confirmed-both-sides service this investigation found.
Method: `python3 -c` script over `plistlib.load(open('prelink/info.plist','rb'))`,
walking every `_PrelinkInfoDictionary` entry's `IOKitPersonalities` for
`IOPropertyMatch` containing `EPICName` or `EPICProviderClass` — 16 hits
total, all listed above.

### Firmware announces the name; no matching `IOPropertyMatch(EPICName=...)` found in this kernelcache

These are real `-epic`-suffixed strings in `dcpfw`, at the same string-table
locality as the confirmed set above (immediately adjacent to
`EPICName`/`EPICProviderClass`/`EPICUnit`/`EPICLocation` property-key
strings, `dcpfw+0x3f6ef6`-`0x3f6f15` and `0x5a7689`-`0x5a76a8`), but this
kernelcache's `IOKitPersonalities` has no entry whose `IOPropertyMatch`
mentions them. Either XNU matches these some other way (raw 32-byte name,
`IONameMatch`, or a class this project didn't grep for), or they are
internal-only channels never exposed past `AFKEndpointInterface` to a
concrete IOKit driver.

| name | firmware evidence | notes |
|---|---|---|
| `dcpexpert-epic` | `dcpfw+0x3e7d60` | `DCPExpertEPClient.cpp` (`+0x4b3d6a`) implements it — see "dcpexpert-epic" below. `AppleDCPExpert` (the only kext-side class with "expert" in its name) binds by `IONameMatch dcp-expert-v1` on `AppleARMIODevice`, **not** by `EPICName` — plausibly opens this channel itself, internally, once already instantiated, rather than through a separate `IOKitPersonalities` match. |
| `disp0-epic` | `dcpfw+0x3e7928` | Paired DT node names nearby: `dispext-service` (external variant), `power-gate-dbe-disp0`/`-dispext0`. `AppleCLCD2`'s `IONameMatch` is `['disp0,t8140', 'dispext0,t8140']` (device-tree name, unrelated match path) — no `EPICName` match for `disp0-epic` found. |
| `system-epic` | `dcpfw+0x3e77e7` | Raw name `"system"` sits immediately before it (`+0x3e77b0`) with property-key-looking neighbors `AFKDiagnostics`/`HeapUsage`/`ThreadInfo`/`compartment` — matches Linux's `systemep.c` `"system"` service closely; likely the RTKit-standard first-endpoint diagnostic/setProperty channel the AFK doc describes, just not matched via `EPICName` on this kernelcache (Linux's own driver doesn't match this one by `EPICName` either — see the AFK doc's §2.2 note that Linux prefers `EPICProviderClass`). |
| `test-epic` | `dcpfw+0x3e7800` | Paired with `test-service`/`test-ep`. `AFKEchoTestEPIC` (`com.apple.driver.AppleFirmwareKit`) exists but matches `IONameMatch=['ap_echo-test']`, a different string — probably a different, generic AFK self-test channel, not this one. |
| `static-epic` | `dcpfw+0x4a9caf`, `+0x5b1ee6` | Sits inside `DCPAVEPClient.cpp`'s string cluster, next to `dcpav-epic-response`, `dispext`/`disp` unit-selector strings, and an `ASSERT!%s:%d getResponseQueue`. Purpose unclear — possibly a fallback/default `EPICName` `DCPAVEPClient` uses before a real per-instance name is assigned, not a real standalone service. |
| `powerlog-epic` | `dcpfw+0x3e7eae` | Paired with `powerlog-service` and `afksmc-service`. Matches Linux's `powerlog-service` almost exactly by name (`docs/re/afk-epic-references.md`'s exclusion list already named this one from the Linux source). |
| `dcpav-audio-arc-epic` | `dcpfw+0x3f882c` | Paired with short name `dcpav-arc-ep`; near HDMI-ARC-sounding neighbors (`DPRXEQ`, `cHDMIb`, `FilterThresh`) — HDMI eARC audio return channel, external-display-only. |
| `dcpav-epic-response` | `dcpfw+0x4a9d03`, `+0x5b1f3a` | Sits right next to `DCPAVEPClient.cpp`'s `getResponseQueue` assert — this is very likely a queue/object *name* used internally by `DCPAVEPClient` (e.g. the name of its response-tracking dictionary), not a channel `EPICName` at all. Flagged as probably-not-a-service. |
| `aop-test-control-epic` | `dcpfw+0x3e7ee3` | Neighbors `aop-route-embedded`/`aop-test` — AOP (audio coprocessor) test/diagnostic control, cross-coprocessor AFK usage (see `common_afk.cpp` notes below). Not display-critical. |
| `cb-ap-to-dcp-service-epic` | `dcpfw+0x3e7a7d` | Paired short name `cb-ap-to-dcp-service-ep`/raw node `cb-ap-to-dcp-service-nub`. `CBAPServiceEPClientiOS.cpp`/`CBAPToDCPService.cpp` implement it — see "CB subsystem" below. |
| `cbrootservice-epic` | `dcpfw+0x3e79a7` | Paired with `cbroot-service`/`cbauto-service`/`cbcolor-service`/`cbxtalk-service` (ambient-light/color-balance sub-nodes). `CBRootServiceiOS.cpp` implements it. |

### Not AFK/EPIC at all: `iomfb-link`

A tightly-packed, NUL-separated string table at `dcpfw+0x3e7f98`–`0x3e8158`
(26 entries, immediately followed by unrelated boot data `"RTKSTACK"`/
`"Power"`/a base36 charset table) lists short endpoint/channel names in this
exact order:

```
dcpexpert-tb-ep, dptx-monitor-tb-ep, mipi-controller-tb-ep, aon-hpd-tb-down-ep,
aon-hpd-tb-up-ep, dcptrustedclient-tb-ep, disp-health-mon-tb-ep,
management-ep, syslog-ep, kdebug-ep, tracekit-ep, oslog-ep, report-ep,
iomfb-link, system-ep, md-ep, test-ep, dcpexpert-ep, disp0-ep,
dcpav-controller-ep, dcpav-power-ep, dcpav-sac-ep, dcpav-device-ep,
dcpav-service-ep, dcpav-interface-ep, dcpdptx-port-ep, dcpdptx-hdcp-ep,
cbservice-ep, cb-ap-to-dcp-service-ep, aop-comms-ep
```

`iomfb-link` (`dcpfw+0x3e8063`) is this firmware's own literal name for the
endpoint XNU's kernelcache calls `DCPEndpoint24`/binds
`AppleDCPLinkServiceSoC` to (confirmed from `IOKitPersonalities`:
`com.apple.driver.AppleMobileDispH17P-DCP`'s `AppleDCPLinkServiceSoC`
personality, `IONameMatch = ['DCPEndpoint24', 'DCPEXTEndpoint24']`). This is
new, direct firmware-side confirmation that the name exists and that this
firmware groups it with the RTKit-standard endpoints
(`management`/`syslog`/`kdebug`/`tracekit`/`oslog`/`report`) rather than with
the `dcpav-*`/`dcpdptx-*` AFK sub-services that follow it in the same list —
consistent with `docs/re/afk-epic-references.md`'s exclusion of it from the
AFK/EPIC framing. **Caution**: this list's order is evidence only that these
26 names are declared together in some array in this order; it was not
disassembled, so it should not be read as proof of ascending numeric mailbox
endpoint order (RTKit standard endpoints are known to be low numbers `0x01`-
`0x0a` per `docs/re/dcp-bringup.md`, but `iomfb-link`/`DCPEndpoint24` is
known to be `0x37`, the *last* AFK-range endpoint — the two facts are not
reconcilable under a "list order = endpoint number order" reading, so that
reading is rejected here even though the adjacency itself is real).

## Command/argument surface found

No numeric group/command opcode table was recovered — this needs
disassembly with a resolved vmaddr↔fileoff mapping (see "Bundle layout"),
which this investigation could not build cheaply, and the shipped firmware
has no retained C++ symbol table to shortcut it with. What **was** found by
string mining, all inside `dlon#2`'s `__TEXT,__const`/`__cstring` in the
region `0x49dxxx`-`0x4bxxx` (duplicated in `dlon#3` around `0x5axxx`-
`0x5bxxx`): a set of Objective-C-style runtime type-encoding strings
(`"v64@?0{mipi_displaytiming_s=IIIIIIIIIIIIII}8"` etc.) that describe
**argument/result struct layouts** for a set of named Tightbeam RPC methods.
These are cross-domain (dlon#2 ↔ dlon#3) calls, not necessarily the AP-facing
EPIC wire format, but they are the best evidence available for what data
several `-epic` services actually carry, since the same class names
(`DCPExpertEPClient`, `AppleDCPSynopsysMIPIDSIController`) sit right next to
them:

| struct / method | type encoding | fields (as decoded) | evidence |
|---|---|---|---|
| `dcpexpert_setinterruptrouteresult_s` | `Q` | one `uint64` | `dcpfw+0x4b4400`; paired with `dcpexpert_dcpinterruptroute_s=Q` at `+0x5aa539` — `DCPExpertEPClient`'s interrupt-routing setup call, see below |
| `dcptrusted_interface_notifyrunmode__result_s` | `C(?=B)` | `char` status + block returning `bool` | `dcpfw+0x4b4463` |
| `dcptrusted_interface_notifydisplaypower__result_s` | `C(?=B)` | same shape | `dcpfw+0x4b44b5` |
| `dptx_dptxtberror_s` | `Q` | one `uint64` error code | `dcpfw+0x49de75` |
| `dptx_coverglassserialnumber_s` | `{dptx_dptxtberror_s=Q}{...lengthtype_s=Q}[44C]` | error + length + 44-char serial string | `dcpfw+0x49dee1` |
| `displayhealthmonitor_displayhealthstats_s` | 136 bytes: error(`Q`) + 10×`Q` + 5×`I` + 2×`Q` | health-stat counters | `dcpfw+0x4a9579` |
| `displayhealthmonitor_failsafestats_s` | 256 bytes: error(`Q`) + `[20I]` + `[20Q]` | 20-entry failsafe counter table | `dcpfw+0x4a9600` |
| `mipi_displaytiming_s` | `IIIIIIIIIIIIII` | 14×`uint32` — DSI mode timing (pixel clock/h/v active/porch/sync, exact field order not decoded) | `dcpfw+0x4a9d3c` |
| `mipi_rawpanelid_s` | `[15C]B` | 15-byte panel ID + 1-byte flag | `dcpfw+0x4a9d69` |
| `mipi_cgsn_s` | `[44C]B` | 44-char string + flag — plausibly a coverglass/chip serial number | `dcpfw+0x4a9d8b` |
| `mipi_extendedid_s` | `[25C]B` | 25-byte extended panel ID + flag | `dcpfw+0x4a9da7` |
| `mipi_sacaggressortableentries_s` | `BCC[9{mipi_agileclockingdata_s=IQ}]` | 9-entry table of `{uint32,uint64}`, "SAC" = spread-spectrum/aggressor coordination between the display clock and other RF-sensitive radios (inferred from neighboring strings `"eDP SAC frequency = %llu"`, `"SAC enable: DP=%u"`, `"failed allocate aggressor table index"` — not an official Apple expansion of the acronym) | `dcpfw+0x4a9dd2` |
| `mipitool_interface_{send,receive}{short,long}command__result_s` | `C(?=II)` / `C(?=I{...[256C]I})` | raw MIPI-DCS command pass-through result shapes | `dcpfw+0x4a9eec`-`0x4a9fe4` |

The last row is backed by concrete method signatures on the class that
actually owns the DSI link:

```
4aa71b IOReturn AppleDCPSynopsysMIPIDSIController::_sendSWMPRShortCommandGated(uint8_t, uint8_t, uint8_t)
4aa7ff IOReturn AppleDCPSynopsysMIPIDSIController::_sendShortCommandGated(uint8_t, uint8_t, uint8_t)
4aa8f5 IOReturn AppleDCPSynopsysMIPIDSIController::_sendSWMPRLongCommandGated(uint8_t, const uint8_t *, size_t)
4aa95e IOReturn AppleDCPSynopsysMIPIDSIController::_sendLongCommandGated(uint8_t, const uint8_t *, size_t)
4aa9f7 IOReturn AppleDCPSynopsysMIPIDSIController::_receiveShortCommandGated(uint8_t, uint8_t *)
4aab1f IOReturn AppleDCPSynopsysMIPIDSIController::_receiveLongCommandGated(uint8_t, uint8_t *, size_t *)
```

and confirmed to ride over AFK specifically (not just Tightbeam) by
`[AFK]`-tagged log lines using the same verb names:
`fdabbc [AFK]receiveShortCommand status=0x%x`, `fdac05 [AFK]sendShortCommand
status=0x%x`, etc. — i.e. **raw MIPI-DCS short/long command send/receive is
itself exposed as (part of) an EPIC service**, most plausibly
`dcpmipi-controller-epic` given `DCPMIPIControllerClient` (`dcpfw+0x4a9d17`)
sits in the same string cluster as these `mipitool_*` type encodings.
`DCPMIPIToolClient`/`MIPIToolInterfaceTBClient`/workloop name `mipitool-ep-wl`
(`dcpfw+0x599948`) suggest there may be a **separate, tooling-only** channel
for raw command passthrough distinct from the driver's normal operation —
not disambiguated here.

`DCPExpertEPClient`'s two log lines are the clearest evidence for what that
channel actually does day to day:

```
fea38e [AFK]DCPExpertEPClient::handleCommand: service=0x%llx, count=%zu
fea3ff [AFK]DCPExpertEPClient::handleCommand: service=0x%llx retrieving health stats
```

— combined with `dcpexpert_dcpinterruptroute_s`/`setinterruptrouteresult_s`
above, `dcpexpert-epic` is most likely used for **interrupt-route
configuration and health-stat retrieval** between AP and firmware, run after
the coprocessor is already up (this is consistent with
`docs/re/dcp0-expert.md`'s finding that `AppleDCPExpert` on the AP side
never gets far enough in this project's current emulation to reach any AFK
traffic at all — the single observed MMIO write is upstream of all of this).

## Endpoint 0x37 (`iomfb-link` / `DCPEndpoint24`)

`docs/re/afk-epic-references.md` explicitly excludes this endpoint as "a
distinct RPC-with-callbacks framing... layered directly on RTKit, not on
AFK/EPIC." This investigation did not overturn that, but did find more
detail from the firmware side:

- The literal name is `iomfb-link` (`dcpfw+0x3e8063` — see the endpoint-name
  table above), Apple's own name for what XNU calls `DCPEndpoint24`.
- The classic macOS-era public reverse-engineering convention for this
  protocol's RPC method IDs is 4-character tags like `A029`/`D001`
  (AP→firmware / firmware→AP). This firmware retains a handful of literal
  `[AD]\d{3}_callback__`-suffixed strings — `A119_callback__`,
  `A120_callback__`, `A122_callback__`, `A355_callback__`, `A374_callback__`,
  `A375_callback__`, `A441_callback__`, `A442_callback__`, `A444_callback__`,
  `A450_callback__` (`dcpfw+0x4c67ed`-`0x4c0e9d`, all inside `UPPipe`/
  `UnifiedPipeline`/pixel-pipeline `.cpp` regions) — but this is at most 10
  of what is publicly known (from macOS-era reverse engineering, not cited
  here as this project's own finding) to be a much larger table; most method
  dispatch in this firmware is evidently **not** done through fully-spelled
  literal strings, so a `strings` pass cannot recover the full table.
- `IOMobileFramebuffer`'s own class carries readable `virtual` method
  signatures at assert/log sites (not a full symbol table — see "Bundle
  layout"): `swap_submit_dcp(const IOMFBSwap *, ...)`,
  `swap_submit_dcp_main(...)`, `get_color_remap_mode(...)`,
  `hotPlug_notify(uint64_t)`, `getDebugInfoBufferJob(uint32_t*, uint32_t*)`,
  `init()`, `start_ios_gated()` (`dcpfw+0x4bab38`-`0x4bb819`). A firmware→AP
  notification path is confirmed by the literal string
  `batched_swap_complete_ap_gated` (`dcpfw+0x4c0d6e`, in
  `IOMobileFramebuffer_LocalCalls.cpp`) — the `_ap` suffix is this
  codebase's own convention for "goes to the AP", consistent with the AFK
  doc's direction-inference method (§1.1) but for a different transport.
- `IOMobileFramebuffer_RemoteCalls.cpp` logs `"IOMFB: failed to serialize
  AFKArray"` (`dcpfw+0x4c0d9e`) and validates argument-array bounds
  (`"%s: indexes: in_args->idx_count (%lu) > in_args->indexes[] count
  (%lu)"`, `+0x4c0de7`) — evidence the call ABI is a generically-typed,
  variable-length in/out argument array (consistent with the publicly
  understood shape of this protocol), not evidence this specific project
  independently reverse-engineered the wire header.
- A **separate**, AFK-framed channel exists for AOP (audio coprocessor)
  integration with the framebuffer: `IOMFBAOPDisplayManagerEPICClient.cpp`
  (`dcpfw+0x4d4a5a`) with log strings `"%s: cannot find AFK endpoint"`,
  `"%s Connected AOP Display-Manager Interface: %p %p"`
  (`dcpfw+0x4d4a7f`-`0x4d4b2e`) — this is Always-On-Display integration
  (AOP renders the always-on clock/widgets and needs to coordinate with the
  main display pipeline), not needed for basic panel bring-up.

**Bottom line for endpoint 0x37**: still not modelled beyond confirming its
firmware-side name and that it is a distinct, generically-typed RPC-array
protocol layered directly on the RTKit mailbox (no AFK ring, no `epic_hdr`).
Building a working impersonation of it needs either (a) full disassembly of
`IOMobileFramebuffer_RemoteCalls.cpp`'s message-header parsing with a solved
vmaddr mapping, or (b) reusing the publicly-documented macOS-era `A000`-style
tag scheme as a starting hypothesis and validating it against this firmware's
`10` surviving literal tags — neither was attempted here.

## Module map (298 `.cpp` files, by rough subsystem)

Full list mined via `grep -io '[A-Za-z0-9_]*\.cpp' dcpfw_strings.txt | sort -u`
against a `strings -a -t x -n 4 dcpfw` dump. Grouped for the minimal-set
recommendation below (a file appearing in a group is not proof every
function in it runs at boot, only that the subsystem exists):

| group | representative files | relevance |
|---|---|---|
| AFK/EPIC transport core | `AFKEP*.cpp`, `AFKEndpointInterface*.cpp`, `AFKDictionary/Array/Number/String/Serialize/Object.cpp`, `afk_interface_transport.cpp`, `afk_messenger_{aop,common,standard}.c`, `common_afk.cpp`, `AFKPlatformExpert.cpp` | **required**, generic to every service |
| RTKit/Tightbeam plumbing | `AFKWorkloop*.cpp`, `AFKEventSource*.cpp`, `AFKMemoryDescriptor*.cpp`, `afkmem_rtkit.cpp`, `afktp_rtkit.cpp`, `pmc.cpp`, `pmgr.cpp`, `pmgr_t8140.cpp`, `rtkpmc_t8140.cpp` | **required** |
| `dcpexpert-epic` | `DCPExpert*.cpp` (8 files incl. `DCPExpertEPClient.cpp`, `DCPExpertSecureInterface{Client,Server}.cpp`, `DCPExpertT8140.cpp`) | likely required (interrupt routing) |
| generic AV stack (`dcpav-*-epic`) | `DCPAV*.cpp` (23 files), `AppleDCPAVPowerController.cpp` | **required** for any panel, internal or external |
| MIPI/DSI internal panel | `AppleDCPSynopsysMIPIDSIController{,Trusted}.cpp`, `AppleDCPMIPIPanel.cpp`, `AppleDCPMIPIPowerController.cpp`, `AppleDCPMIPIT8140UntrustedPowerController.cpp`, `AppleDCPMIPICitra.cpp`, `AppleDCPMIPIDisplaySACController{,Serializer}.cpp`, `DCPMIPIControllerServer.cpp`, `DCPMIPIUntrustedVideoInterface.cpp`, `DCPMIPIVideoInterfaceCommandSerializer.cpp`, `MIPIControllerClient.cpp`/`Server.cpp`, `DCPUntrustedMIPIControllerEPClient.cpp` | **required**, this is the internal-panel path |
| brightness/backlight | `Brightness{EDRHandler,LCD,LCDConversion,OLED,Util}.cpp`, `ControllerIW7042.cpp`, `ControllerLP8549.cpp` | likely required for a usable (non-black) panel; `*OLED*` is the relevant variant on iPhone 16 |
| external DisplayPort/HDMI/USB-C-alt | `AppleDCPDPTX*.cpp` (20 files), `AppleDCPT8112/8120/8122/8132/8140*.cpp`, `AppleDCPDP2HDMI.cpp`, `AppleDCPBaobabExternalDisplay.cpp`, `AppleDCPExternalDisplay.cpp`, `DCPDP*.cpp` (7 files), `AppleDCPMCDP2900/29XX.cpp` (redriver chips), `AppleDCPPS186/190.cpp` | **excludable** for an internal-panel-only bring-up |
| HDCP | `AppleDCPDPTXHDCP{1,2}Controller.cpp`, `AppleDCPDPTXHDCPController.cpp` | **excludable** (external-display content-protection only — no HDCP on the internal panel path) |
| audio (incl. ARC/eARC, AOP) | `AONHPD{Client,Server}.cpp`, `AUC.cpp`, `AOPStatsHandler.cpp`, `CB*.cpp` (11 files — color-balance/ambient tied to AOP), `IOMFBAOPDisplayManagerEPICClient.cpp` | **excludable** |
| CEC | (implied by `dcpav-cec-interface-epic` only — no standalone `.cpp` filename found for it) | **excludable** |
| pixel pipeline / UnifiedPipeline (`UP*.cpp`, ~40 files) | `UnifiedPipeline*.cpp`, `UPPipe*.cpp`, `UPBlock_*.cpp` (24 blocks: gamma, dither, PCC/PCC2D color correction, WPC white-point, VFTG timing generator, GenPipe, etc.), `UPPipeDCP_H17P{,_ext,_int}.cpp` | **required** — this is the actual display-pipe hardware programming behind `iomfb-link`; note the explicit `_int`(ernal)/`_ext`(ernal) split confirms which half matters for a built-in panel |
| MIPI raw-command tooling | `MIPIControllerClient/Server.cpp` overlap with the driver path above; `DCPMIPIToolClient` region only reachable via a separate `mipitool-ep-wl` workloop | tooling, **excludable** |
| color/gamut science | `GainMapHandler.cpp`, `GainTable.cpp`, `GamutMapperHandler.cpp`, `ICCHandler.cpp`, `FxMatrix.cpp`, `UniformityCompensator.cpp`, `TableCompensator.cpp` | likely required for correct-looking output, not for getting *a* picture up |
| display health monitoring | `DCPDisplayHealthMonitor{,Client,Server}.cpp`, `DisplayHealthMonitorClient/Server.cpp` | optional telemetry, **excludable** for first light |
| M3/mailbox/diagnostics | `M3DiagsHandler.cpp`, `CtrlMailboxHandler.cpp`, `MailboxHandler.cpp`, `MailboxChannel.cpp`, `PCC2DMailboxHandler.cpp`, `VideoModeMailboxHandler.cpp` | internal to the M3 pixel-pipeline microcontroller inside DCP, not the AP link — required for the pipeline to run, irrelevant to what we announce over AFK |

## Minimal set for first light on the internal MIPI-DSI panel

Based on the module map and the confirmed EPICName table:

**Required AFK services to announce:**
- `system-epic` (RTKit-standard first service — bring this up regardless, matches Linux's mandatory `systemep_init()`)
- `dcpexpert-epic` (interrupt routing / health stats — `AppleDCPExpert` already blocks earlier in this project's boot per `docs/re/dcp0-expert.md`, but once past that, this is likely next)
- `disp0-epic` (internal-panel `disp0` node, as opposed to `dispext-service`)
- `dcpav-controller-epic`, `dcpav-device-epic`, `dcpav-service-epic`, `dcpav-video-interface-epic`, `dcpav-power-epic` (the five confirmed-both-sides generic AV services — every `EPICName` here has a real `DCPAVFamilyProxy` class waiting to bind)
- `dcpmipi-controller-epic` (the MIPI-DSI-specific control surface, confirmed both-sides, `IOMIPIFamily`/`DCPMIPIControllerProxy`)

**Plausibly required, unconfirmed on the AP side:**
- `dcpav-sac-epic` (spread-spectrum/backlight coordination — unclear if the panel stays dark without it)
- Endpoint `0x37`/`iomfb-link` itself, which is not AFK at all and is not modelled by this document (see above) — **this is very likely the actual hard blocker for getting pixels out**, independent of which AFK services are announced

**Excludable for internal-panel-only bring-up** (all evidence above): every
`dcpdptx-*`/`dcpdp-*`/`dcpdptx-hdcp-*` service (external DP/HDMI/USB-C-alt +
HDCP), `dcpav-audio-interface-epic`, `dcpav-cec-interface-epic`,
`dcpav-audio-arc-epic` (audio/CEC/eARC), `aop-test-control-epic`,
`cb-ap-to-dcp-service-epic`, `cbrootservice-epic` (AOP/ambient-color,
cross-coprocessor features), `powerlog-epic` (telemetry), `test-epic`/
`static-epic` (unclear purpose, low risk to omit first).

## Open questions

- **No numeric group/command opcode was recovered for any service.** This
  needs either a solved vmaddr↔fileoff mapping for `dlon#2`/`dlon#3` (this
  project fitted per-segment biases empirically for `__TEXT`/`__DATA` of one
  image only, and did not attempt `LC_SYMTAB`/`LC_DYSYMTAB` resolution — see
  "Bundle layout") or a from-scratch structural parse of the `dlon`
  Mach-O load commands against a byte-exact model of Apple's firmware
  packer, which is out of scope for a strings-first pass.
- **`dcpexpert-epic`, `disp0-epic`, `system-epic`, `test-epic`,
  `static-epic`, `powerlog-epic`, and the `cb-*`/`aop-*` services have no
  confirmed `EPICName`-matching `IOKitPersonalities` entry** in this
  kernelcache. Either they match some other way (raw 32-byte announce name,
  a class this project's plist grep missed, `IONameMatch` instead of
  `IOPropertyMatch`) or XNU genuinely never surfaces them past
  `AFKEndpointInterface`. Settling this needs disassembling
  `AFKEndpointInterface::probe`/`start` in `com.apple.driver.AppleFirmwareKit`
  to see every matching strategy it tries, not just the `IOPropertyMatch`
  case this investigation grepped for.
- **Endpoint 0x37's actual header/opcode format** is still unknown beyond
  "a generically-typed in/out argument array, not AFK-framed" — see
  "Endpoint 0x37" above for what would settle it.
- **Whether `sendOpenReport`/`sendCloseReport` are real, distinct EPIC report
  subtypes** beyond the AFK doc's `ANNOUNCE`(`0x30`)/`TEARDOWN`(`0x32`), and
  if so their numeric subtype values — not found as literal small-integer
  constants near these strings; needs disassembly.
- **Whether Tightbeam (the `dlon#2`↔`dlon#3` trusted/untrusted IPC this
  investigation found) has any AP-visible surface at all**, or is purely
  internal to the coprocessor. If purely internal, our model never needs to
  implement it — but this was not confirmed either way.
- **`mipi_displaytiming_s`'s exact 14-field order** (pixel clock vs. h/v
  active/porch/sync placement) was not decoded — the type encoding gives
  count and primitive type (`I` = `uint32`) only, not field names.
- The `dlon#1` image (tiny, `vmaddr 0xffffffff00000000`, no symbols) was not
  investigated at all — plausibly the earliest-stage boot/monitor code, but
  this is a guess from section names (`__head`, `__tunables`,
  `__kernel_stack`) only.
