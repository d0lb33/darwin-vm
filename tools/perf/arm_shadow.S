/* Native guest-EL2 Apple TPIDR_GL2 model experiment, NOT an iOS shim.
 * MODE 0: decode real UNDEF in guest vector; 1: per-access SMC to VMM;
 * MODE 2: site-specific branch thunks, preserving scratch registers.
 * MODE 3: EL1 UDF control, using the same full-GPR save/decode/restore shape.
 * x0 iterations. Each iteration reads the shadow register, increments, writes.
 * Guest exception handler preserves every GPR other than the decoded output.
 */
.text
.p2align 4
start:
    mov x11, #0
loop:
#if MODE == 3
    .inst 0x00000029 /* UDF control: read into x9 */
#elif MODE == 0
    mrs x9, S3_6_C15_C11_1
#elif MODE == 1
    smc #0x10
#else
    b read_thunk
#endif
read_return:
    add x10, x9, #1
#if MODE == 3
    .inst 0x0000012a /* UDF control: write from x10 */
#elif MODE == 0
    msr S3_6_C15_C11_1, x10
#elif MODE == 1
    smc #0x11
#else
    b write_thunk
#endif
write_return:
    eor x11, x11, x9
    ror x11, x11, #7
    subs x0, x0, #1
    b.ne loop
    smc #0x7f
    b .
read_thunk:
    stp x12, x13, [sp, #-16]!
    adr x12, shadow
    ldr x9, [x12]
    ldp x12, x13, [sp], #16
    b read_return
write_thunk:
    stp x12, x13, [sp, #-16]!
    adr x12, shadow
    str x10, [x12]
    ldp x12, x13, [sp], #16
    b write_return
.org 0x1000
vectors:
.rept 16
    b handler
    .space 124
.endr
handler:
    sub sp, sp, #256
    stp x0, x1, [sp, #0]
    stp x2, x3, [sp, #16]
    stp x4, x5, [sp, #32]
    stp x6, x7, [sp, #48]
    stp x8, x9, [sp, #64]
    stp x10, x11, [sp, #80]
    stp x12, x13, [sp, #96]
    stp x14, x15, [sp, #112]
    stp x16, x17, [sp, #128]
    stp x18, x19, [sp, #144]
    stp x20, x21, [sp, #160]
    stp x22, x23, [sp, #176]
    stp x24, x25, [sp, #192]
    stp x26, x27, [sp, #208]
    stp x28, x29, [sp, #224]
    stp x30, xzr, [sp, #240]
#if MODE == 3
    mrs x0, esr_el1
#else
    mrs x0, esr_el2
#endif
    lsr x0, x0, #26
    cbnz x0, error
#if MODE == 3
    mrs x0, elr_el1
#else
    mrs x0, elr_el2
#endif
    ldr w1, [x0]
    and w2, w1, #0xffffffe0
    and x3, x1, #31
    adr x4, shadow
#if MODE == 3
    mov w5, #0x20
#else
    mov w5, #0xfb20
    movk w5, #0xd53e, lsl #16
#endif
    cmp w2, w5
    b.eq do_read
#if MODE == 3
    mov w5, #0x120
#else
    movk w5, #0xd51e, lsl #16
#endif
    cmp w2, w5
    b.ne error
    ldr x5, [sp, x3, lsl #3]
    str x5, [x4]
    b advance
do_read:
    ldr x5, [x4]
    str x5, [sp, x3, lsl #3]
advance:
    ldr x5, [x4, #8]
    add x5, x5, #1
    str x5, [x4, #8]
    add x0, x0, #4
#if MODE == 3
    msr elr_el1, x0
#else
    msr elr_el2, x0
#endif
    ldp x0, x1, [sp, #0]
    ldp x2, x3, [sp, #16]
    ldp x4, x5, [sp, #32]
    ldp x6, x7, [sp, #48]
    ldp x8, x9, [sp, #64]
    ldp x10, x11, [sp, #80]
    ldp x12, x13, [sp, #96]
    ldp x14, x15, [sp, #112]
    ldp x16, x17, [sp, #128]
    ldp x18, x19, [sp, #144]
    ldp x20, x21, [sp, #160]
    ldp x22, x23, [sp, #176]
    ldp x24, x25, [sp, #192]
    ldp x26, x27, [sp, #208]
    ldp x28, x29, [sp, #224]
    ldp x30, xzr, [sp, #240]
    add sp, sp, #256
    eret
error:
    smc #0x7e
    b .
.org 0x20000
shadow:
    .quad 0x1234
    .quad 0
