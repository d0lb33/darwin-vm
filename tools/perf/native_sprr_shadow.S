/* Native MMU-on access/fault payload for a candidate SPRR shadow mapper. */
#ifdef NATIVE_SPRR_EL1
#define TTBR_REG TTBR0_EL1
#define TCR_REG TCR_EL1
#define MAIR_REG MAIR_EL1
#define VBAR_REG VBAR_EL1
#define SCTLR_REG SCTLR_EL1
#define ESR_REG ESR_EL1
#define FAR_REG FAR_EL1
#define ELR_REG ELR_EL1
#define SPSR_REG SPSR_EL1
#else
#define TTBR_REG TTBR0_EL2
#define TCR_REG TCR_EL2
#define MAIR_REG MAIR_EL2
#define VBAR_REG VBAR_EL2
#define SCTLR_REG SCTLR_EL2
#define ESR_REG ESR_EL2
#define FAR_REG FAR_EL2
#define ELR_REG ELR_EL2
#define SPSR_REG SPSR_EL2
#endif
.text
.p2align 12
.globl _sprr_begin, _sprr_virtual, _sprr_return, _sprr_vectors, _sprr_helper, _sprr_end
_sprr_begin:
#ifndef NATIVE_SPRR_EL1
    msr HCR_EL2, x0
#endif
    isb
    msr TTBR_REG, x21
#ifdef NATIVE_SPRR_EL1
    msr TTBR1_EL1, x21
#endif
    msr TCR_REG, x22
    msr MAIR_REG, x23
    msr VBAR_REG, x24
    mov x9, #0x1005
    msr SCTLR_REG, x9
    isb
    smc #0x41
    br x25
_sprr_virtual:
    adr x25, _sprr_return
    cmp x27, #0
    b.eq Lread
    cmp x27, #1
    b.eq Lwrite
    br x26
Lread:
    ldr x4, [x26, #0x80]
    b _sprr_return
Lwrite:
    mov x4, #0xdef
    str x4, [x26, #0x80]
_sprr_return:
    smc #0x42
    b .
.p2align 11
_sprr_vectors:
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
    mrs x9, SPSR_REG
    str x9, [x20, #24]
#ifdef NATIVE_SPRR_EL1
    mov x9, #0
#else
    mrs x9, HCR_EL2
#endif
    str x9, [x20, #32]
    smc #0x77
    b .
.p2align 12
_sprr_helper:
    msr HCR_EL2, x9
    isb
    smc #0x55
    b .
_sprr_end:
