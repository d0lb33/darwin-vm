/* Distinct TTBR0/TTBR1 mappings: test actual VHE translation, not HCR bits. */
#ifdef NATIVE_EL1_MMU
#define VBAR_REG VBAR_EL1
#define TTBR0_REG TTBR0_EL1
#define TTBR1_REG TTBR1_EL1
#define TCR_REG TCR_EL1
#define MAIR_REG MAIR_EL1
#define SCTLR_REG SCTLR_EL1
#define ESR_REG ESR_EL1
#define FAR_REG FAR_EL1
#define ELR_REG ELR_EL1
#else
#define VBAR_REG VBAR_EL2
#define TTBR0_REG TTBR0_EL2
#define TTBR1_REG TTBR1_EL2
#define TCR_REG TCR_EL2
#define MAIR_REG MAIR_EL2
#define SCTLR_REG SCTLR_EL2
#define ESR_REG ESR_EL2
#define FAR_REG FAR_EL2
#define ELR_REG ELR_EL2
#endif
.text
.p2align 12
.globl _vmmu_begin, _vmmu_entry, _vmmu_vectors, _vmmu_end
.globl _vmmu_success, _vmmu_fault, _vmmu_ttbr1
_vmmu_begin:
    msr VBAR_REG, x24
#ifndef NATIVE_EL1_MMU
    msr HCR_EL2, x0
#endif
    isb
    msr TTBR0_REG, x21
    cbnz x27, Lskip_ttbr1
_vmmu_ttbr1:
    msr TTBR1_REG, x22
Lskip_ttbr1:
    msr TCR_REG, x23
    mov x9, #0xff
    msr MAIR_REG, x9
    mov x9, #0x1005
    msr SCTLR_REG, x9
    isb
    br x25
_vmmu_entry:
    ldr x4, [x26]
    str x4, [x20, #32]
#ifdef NATIVE_EL1_MMU
    mov x9, #0
#else
    mrs x9, HCR_EL2
#endif
    str x9, [x20, #24]
    mrs x9, CurrentEL
    str x9, [x20, #40]
_vmmu_success:
#ifndef NATIVE_QEMU_VHE_MMU
    smc #0x42
#endif
    b .
.p2align 11
_vmmu_vectors:
.rept 16
    b Lfault
    .space 124
.endr
Lfault:
    mrs x9, ESR_REG
    str x9, [x20]
    mrs x9, FAR_REG
    str x9, [x20, #8]
    mrs x9, ELR_REG
    str x9, [x20, #16]
#ifdef NATIVE_EL1_MMU
    mov x9, #0
#else
    mrs x9, HCR_EL2
#endif
    str x9, [x20, #24]
    mrs x9, CurrentEL
    str x9, [x20, #40]
_vmmu_fault:
#ifndef NATIVE_QEMU_VHE_MMU
    smc #0x77
#endif
    b .
_vmmu_end:
