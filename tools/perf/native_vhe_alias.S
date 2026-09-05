/* Distinct VBAR banks: equal preseeded vectors cannot prove VHE aliasing. */
.text
.p2align 12
.globl _alias_begin, _alias_end, _alias_stop
_alias_begin:
    mov x9, #0x80000000
    msr HCR_EL2, x9
    isb
    msr VBAR_EL1, x1
    msr VBAR_EL2, x2
    mrs x9, VBAR_EL1
    str x9, [x20]
    mrs x9, VBAR_EL2
    str x9, [x20, #8]
    msr HCR_EL2, x0
    isb
    mrs x9, HCR_EL2
    str x9, [x20, #16]
#ifdef NATIVE_QEMU_ALIAS
    smc #0xd203
#else
    msr VBAR_EL1, x3
#endif
    isb
#ifdef NATIVE_QEMU_ALIAS
    smc #0xd229
#else
    mrs x9, VBAR_EL1
#endif
    str x9, [x20, #24]
    mrs x9, VBAR_EL2
    str x9, [x20, #32]
    mrs x9, HCR_EL2
    str x9, [x20, #40]
    mrs x9, ID_AA64MMFR1_EL1
    str x9, [x20, #48]
_alias_stop:
#ifndef NATIVE_QEMU_ALIAS
    smc #0x41
#endif
    b .
_alias_end:
