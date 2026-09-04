// Apple WFE event stream with IRQs masked, no generic timers and no second CPU.
// Three groups of 16 waits: falling edge, rising edge, nonzero virtual offset.
// Finally disable the stream: the following WFE must not reach the sentinel.
.text
.p2align 4
start:
    adr x20, start
    add x20, x20, #0x18, lsl #12
    msr daifset, #15
    msr S3_4_C14_C3_1, xzr  // CNTHV_CTL_EL2
    msr cnthp_ctl_el2, xzr
    msr cnthctl_el2, xzr
    msr cntkctl_el1, xzr
    mov x1, #0x6f           // bit 6, falling edge, enabled
    msr S3_4_C15_C9_6, x1  // AGTCNTKCTL_EL1
    mov x2, #0
1:
    sevl
    wfe                    // consume local event first
    wfe                    // must be woken by the Apple event stream
    add w2, w2, #1
    str w2, [x20]
    cmp w2, #16
    b.ne 1b
    mov x1, #0x67           // bit 6, rising edge, enabled
    msr S3_4_C15_C9_6, x1
    mov x2, #0
2:
    sevl
    wfe
    wfe
    add w2, w2, #1
    str w2, [x20, #4]
    cmp w2, #16
    b.ne 2b
    mov x1, #37
    msr S3_1_C15_C9_4, x1  // AGTCNTVOFF_EL2
    mov x2, #0
3:
    sevl
    wfe
    wfe
    add w2, w2, #1
    str w2, [x20, #8]
    cmp w2, #16
    b.ne 3b
    msr S3_4_C15_C9_6, xzr
    mov w1, #0x600d
    str w1, [x20, #24]
    sevl
    wfe
    wfe                    // no event stream, timer or interrupt can wake us
    mov w1, #1
    str w1, [x20, #12]      // failure sentinel
4:
    b 4b
