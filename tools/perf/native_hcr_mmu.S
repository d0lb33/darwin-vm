.text
.p2align 12
.globl _mmu_begin, _mmu_virtual, _mmu_after, _mmu_vectors, _mmu_end
.globl _mmu_wait, _mmu_done, _mmu_fault_done
_mmu_begin:
    /* x0=HCR, x20=results VA, x21=root IPA, x22=TCR, x23=MAIR,
     * x24=vectors VA, x25=virtual entry, x26=protection test selector. */
    mrs x1, MIDR_EL1
    mrs x2, MPIDR_EL1
    mrs x3, ID_AA64PFR0_EL1
    mrs x4, ID_AA64ISAR0_EL1
    mrs x5, ID_AA64MMFR0_EL1
    mrs x6, ID_AA64MMFR1_EL1
    msr HCR_EL2, x0
    isb
    mrs x27, HCR_EL2
    msr TTBR0_EL2, x21
    mrs x10, HCR_EL2
    msr TCR_EL2, x22
    mrs x11, HCR_EL2
    msr MAIR_EL2, x23
    mrs x12, HCR_EL2
    msr VBAR_EL2, x24
    mrs x13, HCR_EL2
    mov x9, #0x1005
    msr SCTLR_EL2, x9
    mrs x14, HCR_EL2
    isb
    mrs x15, HCR_EL2
    br x25
_mmu_virtual:
    bl Lsnapshot
    mrs x28, HCR_EL2
_mmu_wait:
#ifdef NATIVE_QEMU_MMU
    b .
#else
    smc #0x41
#endif
_mmu_after:
    add x20, x20, #0x100
    bl Lsnapshot
    cmp x26, #1
    b.eq Lwrite_code
    cmp x26, #2
    b.eq Lexec_data
_mmu_done:
#ifdef NATIVE_QEMU_MMU
    b .
#else
    smc #0x42
    b .
#endif
Lwrite_code:
    mov x9, #0x1234
    and x10, x25, #0xffffffffffffc000
    str x9, [x10]
    smc #0xbad
    b .
Lexec_data:
    br x20
Lsnapshot:
    mrs x9, HCR_EL2
    str x9, [x20]
    mrs x9, SCTLR_EL2
    str x9, [x20, #8]
    mrs x9, TTBR0_EL2
    str x9, [x20, #16]
    mrs x9, TCR_EL2
    str x9, [x20, #24]
    mrs x9, MAIR_EL2
    str x9, [x20, #32]
    mrs x9, VBAR_EL2
    str x9, [x20, #40]
    ret
.p2align 11
_mmu_vectors:
.rept 16
    b Lfault
    .space 124
.endr
Lfault:
    mrs x9, ESR_EL2
    str x9, [x20, #0x40]
    mrs x9, FAR_EL2
    str x9, [x20, #0x48]
    mrs x9, HCR_EL2
    str x9, [x20, #0x50]
_mmu_fault_done:
#ifdef NATIVE_QEMU_MMU
    b .
#else
    smc #0x77
    b .
#endif
_mmu_end:
