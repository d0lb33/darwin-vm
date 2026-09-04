/* Two-core machine test, independent of the XNU/IOPMGR boot dependency.
 * Loaded in the final MiB of RAM in a T8140 machine. No guest image edits.
 * Both cores atomically increment one counter and exchange real FIQ IPIs.
 */
.text
.global _smp_smoke
_smp_smoke:
    bl setup
    /* CPU1 RVBAR and T8140 PMGR cluster-0 start. */
    adr x0, secondary
    orr x0, x0, #1
    mov x1, #0x50000
#ifdef SMP_CROSS_CLUSTER
    movk x1, #0x1105, lsl #16
#else
    movk x1, #0x1015, lsl #16
#endif
    movk x1, #2, lsl #32
    str x0, [x1]
    /* The locked RVBAR must ignore subsequent writes, including this zero. */
    str xzr, [x1]
    dsb sy
#ifdef SMP_CROSS_CLUSTER
    mov x1, #0x400c
#else
    mov x1, #0x4008
#endif
    movk x1, #0x73, lsl #16
    movk x1, #3, lsl #32
#ifdef SMP_CROSS_CLUSTER
    mov w0, #1
#else
    mov w0, #2
#endif
    str w0, [x1]
    mov w0, #1
    stlr w0, [x20]
    bl count
1:  ldr w0, [x20, #8]
    cbz w0, 1b
#ifdef SMP_CROSS_CLUSTER
    mov x0, #0x10000
    msr S3_5_C15_C0_1, x0
#else
    mov x0, #1
    msr S3_5_C15_C0_0, x0
#endif
2:  ldr w0, [x20, #16]
    cbz w0, 2b
    ldr w0, [x20, #20]
    cbz w0, 2b
    mov w0, #0x600d
    str w0, [x20, #24]
3:  wfi
    b 3b

setup:
    adr x20, shared
    mrs x21, mpidr_el1
    and x21, x21, #0xffff
    cmp x21, #0
    cset x21, ne
    add x0, x20, #0x4000
    add x0, x0, x21, lsl #12
    mov sp, x0
    adr x0, vectors
    msr vbar_el2, x0
    mov x0, #0x80000000
    orr x0, x0, #8
    msr hcr_el2, x0
    isb
    msr daifclr, #1
    ret

count:
    mov w1, #20000
    add x3, x20, #12
4:  ldaxr w0, [x3]
    add w0, w0, #1
    stlxr w2, w0, [x3]
    cbnz w2, 4b
    subs w1, w1, #1
    b.ne 4b
    mov w0, #1
    add x3, x20, #4
    str w0, [x3, x21, lsl #2]
    ret

.org 0x4000
secondary:
    bl setup
5:  ldar w0, [x20]
    cbz w0, 5b
    bl count
6:  wfi
    b 6b

.org 0x10000
vectors:
    b unexpected
.org 0x10200
    b unexpected
.org 0x10300
    b fiq
.org 0x10800
unexpected:
    mov w0, #0xbad
    str w0, [x20, #24]
7:  wfi
    b 7b
fiq:
    stp x0, x1, [sp, #-16]!
    mrs x0, S3_5_C15_C1_1
    tbz x0, #0, unexpected
    mov x0, #1
    msr S3_5_C15_C1_1, x0
    add x1, x20, #16
    ldr w0, [x1, x21, lsl #2]
    add w0, w0, #1
    str w0, [x1, x21, lsl #2]
    cbz x21, 8f
    mov x0, #0
#ifdef SMP_CROSS_CLUSTER
    msr S3_5_C15_C0_1, x0
#else
    msr S3_5_C15_C0_0, x0
#endif
8:  ldp x0, x1, [sp], #16
    eret
.org 0x18000
shared:
    .space 32
