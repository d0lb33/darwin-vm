/* Identical EL0 payload for HVF and TCG. Normal WB memory with separate
 * EL1 bootstrap and EL0 aliases, both backed by the same physical memory.
 * Input x0=iterations, x1=case. Output x9=checksum, x21=frequency,
 * x22=elapsed counter ticks, x23=CurrentEL captured before dropping to EL0.
 * No Darwin firmware, OS, syscalls or device model work is measured here. */
.text
.p2align 4
entry:
    msr daifset, #15
    mrs x23, CurrentEL
    mov x15, #0x300000
    msr cpacr_el1, x15
    mov x15, #3
    msr cntkctl_el1, x15
    mov x15, #0xff
    msr mair_el1, x15
    mov x15, #0x3519
    movk x15, #2, lsl #32
    msr tcr_el1, x15
    adr x15, table
    msr ttbr0_el1, x15
    dsb sy
    isb
    mov x15, #0x1805
    movk x15, #0x30d0, lsl #16
    msr sctlr_el1, x15
    isb
    adr x15, ready
    mov x16, #0x40000000
    add x15, x15, x16
    msr elr_el1, x15
    msr spsr_el1, xzr
    eret
.org 0x100
ready:
    isb
    mrs x20, cntvct_el0
    mrs x21, cntfrq_el0
    mov x9, #0x1234
    mov x10, #0x5678
    cmp x1, #1
    b.eq memory_case
    cmp x1, #2
    b.eq neon_case
    cmp x1, #3
    b.eq chase_case
alu_loop:
    add x9, x9, x10
    ror x9, x9, #7
    eor x10, x10, x9
    add x10, x10, #37
    add x9, x9, x10
    ror x10, x10, #13
    eor x9, x9, x10
    add x9, x9, #17
    subs x0, x0, #1
    b.ne alu_loop
    b finish
memory_case:
    adr x2, data
    mov x3, x2
    add x4, x2, #16, lsl #12
memory_loop:
    ldp x9, x10, [x2]
    add x9, x9, #1
    eor x10, x10, x9
    stp x9, x10, [x2], #16
    cmp x2, x4
    csel x2, x3, x2, eq
    subs x0, x0, #1
    b.ne memory_loop
    eor x9, x9, x10
    b finish
neon_case:
    dup v0.2d, x9
    dup v1.2d, x10
neon_loop:
    add v0.2d, v0.2d, v1.2d
    ext v1.16b, v1.16b, v0.16b, #7
    eor v0.16b, v0.16b, v1.16b
    add v1.4s, v1.4s, v0.4s
    add v0.2d, v0.2d, v1.2d
    ext v1.16b, v1.16b, v0.16b, #3
    eor v0.16b, v0.16b, v1.16b
    add v1.4s, v1.4s, v0.4s
    subs x0, x0, #1
    b.ne neon_loop
    umov x9, v0.d[0]
    umov x10, v0.d[1]
    eor x9, x9, x10
    b finish
chase_case:
    adr x2, data
chase_loop:
    ldr x2, [x2]
    ldr x2, [x2]
    ldr x2, [x2]
    ldr x2, [x2]
    ldr x2, [x2]
    ldr x2, [x2]
    ldr x2, [x2]
    ldr x2, [x2]
    eor x9, x9, x2
    subs x0, x0, #1
    b.ne chase_loop
finish:
    isb
    mrs x22, cntvct_el0
    sub x22, x22, x20
    b done
.org 0x1000
done:
    b done
.org 0x10000
table:
    .quad 0
    .quad 0x40000701  /* EL1 bootstrap alias at VA 0x40000000 */
    .quad 0x40000741  /* EL0 alias at VA 0x80000000, same PA 0x40000000 */
.org 0x20000
data:
    .space 0x10000
