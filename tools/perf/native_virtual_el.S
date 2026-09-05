/* Architectural instruction stream for an EL2 -> EL1 -> EL2 round trip.
 * The runner rewrites selected privileged sites, retaining original words.
 * Two HCR reads deliberately remain native to test the exception bridge.
 */
.text
.p2align 14
.globl _vel_begin, _vel_end, _vel_vectors2, _vel_vectors1, _vel_bridge
.globl _vel_hcr2_native, _vel_hcr1_native, _vel_secret_fault, _vel_after_hvc
.globl _vel_done, _vel_failed, _vel_user_after, _vel_user_after2
.globl _vel_user_priv, _vel_user_data, _vel_illegal_target
_vel_begin:
    cbz x19, Linitial_mode
    msr SPSel, #0
Linitial_mode:
    mov x9, sp
    str x9, [x20, #328]
    mrs x5, CurrentEL
    str x5, [x20]
    adr x9, _vel_vectors2
    msr VBAR_EL2, x9
    mov x9, #0x80000000
    movk x9, #4, lsl #32
#ifdef VEL_TGE
    orr x9, x9, #0x8000000
#endif
    msr HCR_EL2, x9
    /* E2H is software state here. The EL1 name must alias the EL2 bank. */
    mrs x9, VBAR_EL1
    str x9, [x20, #8]
_vel_hcr2_native:
    mrs x10, HCR_EL2
    str x10, [x20, #16]
#ifdef VEL_TGE
    cbz x18, Ltge_user
    adr x9, _vel_illegal_target
    msr ELR_EL2, x9
    mov x9, #0x3c5
    movk x9, #0xa000, lsl #16
    msr SPSR_EL2, x9
    eret
_vel_illegal_target:
    mov x14, #0xbad
    b _vel_failed
Ltge_user:
    adr x9, Luser
    and x9, x9, #0x7fffffffff
    msr ELR_EL2, x9
    mov x9, #0x3c0
    movk x9, #0x2000, lsl #16
    msr SPSR_EL2, x9
    eret
#else
_vel_illegal_target:
    adr x9, Lkernel
    msr ELR_EL2, x9
    mov x9, #0x3c5
    movk x9, #0xa000, lsl #16
    msr SPSR_EL2, x9
    eret
#endif
_vel_failed:
    smc #0x43
    b .
.p2align 11
_vel_vectors2:
.rept 16
    b Lmonitor_exception
    .space 124
.endr
Lmonitor_exception:
#ifdef VEL_TGE
    mrs x9, ESR_EL2
    mov x10, #0x3a000000
    cmp x9, x10
    b.ne Lkernel_exception
    str x9, [x20, #336]
    mrs x9, ELR_EL2
    str x9, [x20, #344]
    mrs x9, SPSR_EL2
    str x9, [x20, #352]
    mrs x9, CurrentEL
    str x9, [x20, #360]
    mov x9, sp
    str x9, [x20, #368]
    str x14, [x20, #376]
    adr x9, Ltge_user
    msr ELR_EL2, x9
    mov x9, #0x3c9
    msr SPSR_EL2, x9
    eret
#else
    mrs x12, PAN
    str x12, [x20, #168]
    mrs x9, CurrentEL
    str x9, [x20, #96]
    mrs x9, SPSR_EL2
    str x9, [x20, #104]
    mrs x9, ELR_EL2
    str x9, [x20, #112]
    mrs x9, ESR_EL2
    str x9, [x20, #120]
    mov x9, sp
    str x9, [x20, #128]
    ldr x9, [x26]
    str x9, [x20, #136]
    eret
#endif
.p2align 14
Lkernel:
    mrs x9, NZCV
    str x9, [x20, #24]
    mrs x9, CurrentEL
    str x9, [x20, #32]
    mov x9, sp
    str x9, [x20, #40]
_vel_hcr1_native:
    mrs x10, HCR_EL2
_vel_secret_fault:
#ifndef VEL_TCG_CONTROL
    ldr x4, [x26]
#else
    nop /* Permission enforcement has its separate native SPRR matrix. */
#endif
    mov x9, #0x60000000
    msr NZCV, x9
    hvc #0x71
_vel_after_hvc:
    mov x9, sp
    str x9, [x20, #144]
    mrs x9, NZCV
    str x9, [x20, #152]
    adr x9, Luser
    and x9, x9, #0x7fffffffff
    msr ELR_EL1, x9
    mov x9, #0x3c0
    movk x9, #0x2000, lsl #16
    msr SPSR_EL1, x9
    eret
.p2align 11
_vel_vectors1:
.rept 16
    b Lkernel_exception
    .space 124
.endr
Lkernel_exception:
    mrs x12, PAN
    str x12, [x20, #160]
    mrs x9, ESR_EL1
    mrs x10, ELR_EL1
    mrs x11, SPSR_EL1
    mov x16, #0x72
    movk x16, #0x5600, lsl #16
    cmp x9, x16
    b.eq Lsvc_first
    add x16, x16, #1
    cmp x9, x16
    b.eq Lsvc_second
    tst x11, #0xf
    b.eq Luser_fault
    /* First fault: inaccessible EL2 register. Second: protected data. */
    cbnz x28, Ldata_fault
    str x9, [x20, #48]
    str x10, [x20, #56]
    str x11, [x20, #64]
    b Lreturn_fault
Ldata_fault:
    str x9, [x20, #72]
    str x10, [x20, #80]
    str x11, [x20, #88]
Lreturn_fault:
    add x28, x28, #1
    add x10, x10, #4
    msr ELR_EL1, x10
    eret
Luser_fault:
    mov x16, #0x2000000
    cmp x9, x16
    b.ne Luser_data_fault
    str x9, [x20, #272]
    str x10, [x20, #280]
    str x11, [x20, #288]
    b Luser_fault_return
Luser_data_fault:
    str x9, [x20, #296]
    str x10, [x20, #304]
    str x11, [x20, #312]
    mrs x9, FAR_EL1
    str x9, [x20, #320]
Luser_fault_return:
    add x10, x10, #4
    msr ELR_EL1, x10
    eret
Lsvc_first:
    str x9, [x20, #176]
    str x10, [x20, #184]
    str x11, [x20, #192]
    str x13, [x20, #200]
    str x14, [x20, #208]
    str x15, [x20, #216]
    eret
Lsvc_second:
    str x9, [x20, #224]
    str x10, [x20, #232]
    str x11, [x20, #240]
    str x17, [x20, #248]
    str x18, [x20, #256]
    mrs x9, CurrentEL
    str x9, [x20, #264]
_vel_done:
#ifndef VEL_TCG_CONTROL
    smc #0x42
#endif
    b .
.p2align 14
/* A private host-owned vector bridge. No scratch GPRs or guest stack used. */
_vel_bridge:
.set slot, 0
.rept 16
    smc #(0xef00 + slot)
    b .
    .space 120
.set slot, slot + 1
.endr
.p2align 14
Luser:
_vel_user_priv:
    mrs x10, HCR_EL2
_vel_user_data:
    ldr x4, [x26]
    mrs x13, NZCV
    mov x14, #0xabc
    add x14, x14, #2
    mov x15, sp
    svc #0x72
_vel_user_after:
    mrs x17, NZCV
    mov x18, sp
    svc #0x73
_vel_user_after2:
    b .
#ifdef VEL_TCG_CONTROL
.p2align 14
.globl _vel_tcg_setup
_vel_tcg_setup:
    msr VBAR_EL1, x3
    msr VBAR_EL2, x4
    msr TTBR0_EL1, x1
    msr TTBR1_EL1, x1
    msr TCR_EL1, x2
    mov x9, #0xff
    msr MAIR_EL1, x9
    mov x10, #0x1005
    msr SCTLR_EL1, x10
    msr SP_EL1, x5
    msr SP_EL0, x11
    mov sp, x6
    msr HCR_EL2, x0
    isb
    msr TTBR0_EL2, x1
    msr TTBR1_EL2, x1
    msr TCR_EL2, x2
    msr MAIR_EL2, x9
    msr SCTLR_EL2, x10
    isb
    br x7
#endif
_vel_end:
