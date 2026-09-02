// Same instruction list as `hvf_probe --selftest`, assembled by clang, so the
// hand-encoded words in hvf_probe.c can be diffed against a real assembler.
.text
    mrs x0, S3_6_C15_C1_0          // SPRR_CONFIG_EL1
    msr S3_6_C15_C1_0, x1
    mrs x0, CurrentEL
    mrs x0, S3_0_C11_C9_0          // TAG_OFFSET_EL2
    msr S3_0_C1_C0_6, x1           // GCR_EL1
    movz x0, #0x1234
    movk x0, #0x5678, lsl #16
    movk x0, #0x9abc, lsl #32
    movk x0, #0xdef0, lsl #48
    str x10, [x9, #0x20]
    ldr x3, [x1, #8]
    add x11, x11, #4
    orr x1, x1, x0
    mov x3, x1
    b . + 0x800
    eret
    isb
    nop
    wfi
    svc #0
    hvc #0
    smc #0
    brk #0
    irg x0, x1, xzr
    addg x2, x1, #16, #3
    stg x2, [x2]
    ldg x3, [x3]
    mrs x10, esr_el1
    mrs x11, elr_el1
    msr elr_el1, x11
    mrs x10, esr_el2
    mrs x11, elr_el2
    msr elr_el2, x11
    .long 0x00201420               // genter
    .long 0x00201400               // gexit
    ubfx x12, x10, #26, #6
    cmp x12, #0x15
    b.eq . + 24
