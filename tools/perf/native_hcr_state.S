.text
.p2align 12
.globl _state_begin, _state_after, _state_end, _state_read_hcr, _state_write_hcr
_state_begin:
    /* x0=HCR value, x1=exit type, x20=results in mapped RAM. */
    msr HCR_EL2, x0
    isb
    mov x9, #0x1122
    msr TPIDR_EL2, x9
    adr x9, Lvectors
    msr VBAR_EL2, x9
    and x9, x20, #0xffffffffffffc000
    msr TTBR0_EL2, x9
    mov x9, #0x800000
    msr SCTLR_EL2, x9
    isb
    bl Lsnapshot
    cbz x1, Lsmc
    mov x13, #0x50000000
    str x0, [x13]
    b _state_after
Lsmc:
    smc #0x42
_state_after:
    add x20, x20, #0x100
    bl Lsnapshot
    smc #0x43
    b .
Lsnapshot:
    mrs x9, HCR_EL2
    str x9, [x20]
    mrs x9, TPIDR_EL2
    str x9, [x20, #8]
    mrs x9, VBAR_EL2
    str x9, [x20, #16]
    mrs x9, TTBR0_EL2
    str x9, [x20, #24]
    mrs x9, SCTLR_EL2
    str x9, [x20, #32]
    ret
_state_write_hcr:
    msr HCR_EL2, x9
    isb
_state_read_hcr:
    mrs x9, HCR_EL2
    smc #0x55
    b .
.p2align 11
Lvectors:
.rept 16
    smc #0xdead
    b .
    .space 120
.endr
_state_end:
