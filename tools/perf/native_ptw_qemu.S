/* One real access under TCG, or one software walk under HVF. */
.text
.p2align 14
.globl _ptw_begin, _ptw_stop, _ptw_vectors, _ptw_access
_ptw_begin:
#ifndef PTW_TCG
    smc #0xd300
    stp x0, x1, [x20]
    stp x2, x3, [x20, #16]
    stp x4, x5, [x20, #32]
    str x6, [x20, #48]
    stp x7, x8, [x20, #56]
    b _ptw_stop
#else
    msr daifset, #15
    msr TCR_EL1, x3
    msr TTBR0_EL1, x4
    msr TTBR1_EL1, x5
    msr MAIR_EL1, x7
    adr x9, _ptw_vectors
    msr VBAR_EL1, x9
    dsb sy
    tlbi vmalle1
    dsb sy
    isb
    msr SCTLR_EL1, x6
    isb
    cbnz x2, _ptw_access
    adr x9, _ptw_access
    msr ELR_EL1, x9
    mov x9, #0x3c0
    msr SPSR_EL1, x9
    eret
_ptw_access:
    cmp x1, #1
    b.eq Lwrite
    cmp x1, #2
    b.eq Lexecute
    ldr x16, [x0]
    b Lsuccess
Lwrite:
    str x15, [x0]
    b Lsuccess
Lexecute:
    blr x0
Lsuccess:
    mov x9, #1
    str x9, [x20]
    str x16, [x20, #8]
    b _ptw_stop
.p2align 11
_ptw_vectors:
.rept 16
    b Lfault
    .space 124
.endr
Lfault:
    mrs x9, ESR_EL1
    str x9, [x20, #24]
    mrs x9, FAR_EL1
    str x9, [x20, #32]
    mrs x9, ELR_EL1
    str x9, [x20, #40]
    b _ptw_stop
#endif
#ifndef PTW_TCG
_ptw_access:
_ptw_vectors:
#endif
_ptw_stop:
    b .
