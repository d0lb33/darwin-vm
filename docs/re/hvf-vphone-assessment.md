# vphone-cli reuse assessment — September 5, 2026

Source inspection at upstream commit
`87f796c62a7cb385cd37afce121f6e222d83e5b5`; read-only reference checkout
`/tmp/dvm/HVF_VPHONE_REF1`. No upstream build/setup/install script was run.
This assessment does not establish a local vphone boot, benchmark, host-policy
change, or fix for our physical-iPhone HVF guest.

## What the project supplies

[VPhoneHardwareModel.swift](https://github.com/Lakr233/vphone-cli/blob/87f796c62a7cb385cd37afce121f6e222d83e5b5/sources/vphone-cli/VPhoneHardwareModel.swift)
constructs a private `_VZMacHardwareModelDescriptor`, setting platform version
3, board ID 0x90 and ISA 2. It then asks VZMacHardwareModel whether the model
is supported. These are actual source calls, not evidence that this model is
available through public Hypervisor.framework or with ordinary entitlements.

[VPhoneVirtualMachine.swift](https://github.com/Lakr233/vphone-cli/blob/87f796c62a7cb385cd37afce121f6e222d83e5b5/sources/vphone-cli/VPhoneVirtualMachine.swift)
hands execution to VZVirtualMachine. It configures the Mac platform/bootloader,
an optional custom ROM URL, VZMacGraphicsDeviceConfiguration, virtio block,
sound, entropy and sockets, plus private PL011, touch, battery, accelerators,
SEP and debugger interfaces. VPhoneBundleOps uses the installed framework's
AVPBooter.vresearch1.bin and AVPSEPBooter.vresearch1.bin resources.

This supplies a usable architectural reference for a Mac-specific runtime
adapter and its launch/identity/storage/control tooling. It does not supply
the source implementation of Apple's CPU virtualization or device backends.
VZ owns its VM and devices; it is not a CPU accelerator that can simply be
attached to our existing QEMU Darwin device model.

The upstream [license](https://github.com/Lakr233/vphone-cli/blob/87f796c62a7cb385cd37afce121f6e222d83e5b5/LICENSE)
is MIT and requires preservation of its copyright and permission notice when
copying covered code. Apple firmware/frameworks are separate dependencies.

## Different firmware and machine

The pinned [firmware-origin report](https://github.com/Lakr233/vphone-cli/blob/87f796c62a7cb385cd37afce121f6e222d83e5b5/research/firmware_manifest_and_origins.md)
describes a hybrid guest:

- PCC vresearch101 boot chain and security monitors.
- PCC vphone600 kernel, device tree, SEP and runtime components.
- iPhone17,3 OS filesystem and trust caches.

It lists sptm.vresearch1.release.im4p as unpatched. That does not make the
whole guest unmodified: kernel, boot-chain and filesystem changes depend on
the selected variant. It is not our current t8140 kernel/SPTM/device tree.
The repository README reports testing the iPhone17,3 iOS 27 build 24A5430a
with cloudOS 26.4 23E5207q; that is upstream runtime evidence, not a local run.

The PCC SPTM was already extracted read-only from the official Apple archive
into `/tmp/dvm/HVF_GXF_PCC264/sptm.vresearch1.macho` for research. Its presence
does not establish that it can be substituted into our current QEMU machine.
Earlier empty GXF_CONFIG_EL2 site results must not be interpreted as absence
of GXF: firmware can use the EL1 encoding through VHE redirection.

## Host prerequisites

The source [entitlements](https://github.com/Lakr233/vphone-cli/blob/87f796c62a7cb385cd37afce121f6e222d83e5b5/sources/vphone.entitlements)
request both `com.apple.private.virtualization` and
`com.apple.private.virtualization.security-research`, in addition to the public
virtualization entitlement. The [README](https://github.com/Lakr233/vphone-cli/blob/87f796c62a7cb385cd37afce121f6e222d83e5b5/README.md)
documents research-guest permission and SIP/AMFI relaxation to run its signed
application with those private entitlements. Its less-permissive host option
still relaxes SIP's debugging restriction and applies an AMFI exception.

Thus it is a candidate opt-in research backend, not evidence of a supported
public iOS VM API available on an unchanged Mac. Private APIs and firmware
pairings also need validation across host/guest updates. The present task
does not authorize changing host security policy or rebooting the Mac.

## CPU research follow-up: user's selected direction

The user clarified that the priority is learning Apple's CPU virtualization
mechanisms for our QEMU/HVF backend, rather than adding a separate VZ runtime.
The separate-backend assessment below is an alternative, not the selected plan.

There is a concrete private-HVF lead. Mohamed Mediouni's March 24, 2026
[QEMU RFC series](https://www.mail-archive.com/qemu-devel@nongnu.org/msg1180143.html)
includes a private-ISA patch. The
[follow-up explanation](https://www.mail-archive.com/qemu-devel@nongnu.org/msg1180246.html)
distinguishes vmapple's paravirtualized PAC support from vresearch1/PCC/iOS:
the latter reportedly needs private ISA level 4, including GXF; level 3 is
insufficient. This is an upstream developer report, not a local iOS boot.
Do not equate those HVF numbers with vphone's VZ hardware descriptor ISA 2.

Read-only inspection of this host (macOS 27.0, 26A5421a) confirms:

- Hypervisor `__hv_vm_config_set_isa` at `0x21c312cd8` stores its second
  argument at configuration offset `0x1c` and returns zero for a non-null
  configuration. Getter `0x21c312cf8` reads the same field. Therefore setter
  success alone cannot establish authorization, VM creation or CPU support.
- `HvCore::Hypervisor::VcpuStateManager::get_gxf_config_el1` at
  `0x21c3778d4` checks state byte offset `0x4117`, bit 2, conditionally calls
  `0x21c31b924` with arguments 8 and 0, then loads state offset `0xb68`.
- The corresponding setter at `0x21c3779a4` performs the same conditional
  call, ORs `0x400000000000000` into state offset `0xa08`, and writes the
  value to `0xb68`. This is evidence of cached state with update tracking;
  the kernel-side synchronization and actual guest execution remain untraced.
- Named SPRR configuration getters/setters exist at `0x21c37924c` and
  `0x21c37931c`. Symbol presence does not prove hardware passthrough.
- Apple's installed `com.apple.Virtualization.VirtualMachine.xpc` executable
  carries `com.apple.private.hypervisor` (read using `codesign -d
  --entitlements :-`), in addition to the public hypervisor entitlement.

Evidence outputs: `/tmp/dvm/HVF_PRIVATE_ISA_SYMBOLS1.txt`,
`/tmp/dvm/HVF_PRIVATE_ISA_SET2.disasm`, and
`/tmp/dvm/HVF_PRIVATE_GXF1.disasm`. Addresses refer to this build's
`/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld/dyld_shared_cache_arm64e`.
Reproduce with `ipsw dyld symaddr CACHE --image Hypervisor`, then
`ipsw dyld disass CACHE --vaddr ADDRESS --count COUNT --quiet`.
Avoid full-cache symbol analysis for a known function address.

Our earlier rejected private entitlement probe used
`com.apple.private.hypervisor.vmapple`; it did not call this ISA setter.
It does not settle the availability of the newly identified path. Conversely,
the upstream report requires SIP/AMFI relaxation, so this finding does not
establish access on an unchanged host. No host policy was changed or private
VM launched during this inspection.

Next bounded research: trace the ISA field through VM creation and authorization,
identify the GXF/SPRR synchronization and guest execution path, and determine
whether a permitted capability probe can exercise it. If usable, evaluate it
as an optional Mac HVF path within our machine. Independently reuse verified
register/permission semantics in the portable implementation. TCG remains the
fallback; Windows cannot consume Apple's framework. There is no new boot or
performance result from this static inspection.

## Alternative architecture considered before clarification

Evaluate a separate VZ/PCC Mac backend while retaining the existing portable
QEMU/TCG backend. Share a host-independent VM control interface, configuration,
logs, app/control tooling where suitable, and explicit runtime capabilities.
Keep firmware profiles and VM state tied to their actual backend. Do not
assume a QEMU RAM snapshot, SEP identity/storage, or existing disk can resume
unchanged under VZ; cross-backend transfer would need separate validation.

For Windows ARM, preserve a route to WHPX plus our portable compatibility
and device work. Current [QEMU WHPX documentation](https://www.qemu.org/docs/master/system/whpx.html)
supports ARM64 hosts, but it does not establish compatibility with this iOS
guest. Windows x86 continues to require ARM instruction translation via TCG.
The VZ/PCC backend itself remains Mac-only.

The immediate performance opportunity is avoiding our present experimental
bridge in the Mac runtime and measuring the upstream virtual-platform guest.
No particular speedup or claim of hardware GXF passthrough follows from the
Swift configuration source. The research kernel/firmware differences also
mean timings cannot be presented as a same-image accelerator comparison.

Recommended bounded evaluation: validate platform/host prerequisites; prepare
a separate VM bundle using a pinned upstream revision and explicit firmware
variant; verify native boot; then measure boot, first-boot migration and the
same checked CPU workloads. Record host policy and firmware differences.
Graphics configuration is promising for later work, but source configuration
alone does not establish Metal/game performance. Existing display work remains
owned by its current agent.
