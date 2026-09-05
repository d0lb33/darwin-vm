/* Six-CPU executable remap test. Each phase starts after the preceding
 * phase was acknowledged by every CPU. The writer broadcasts architectural
 * TLBI range maintenance before publishing the next phase with STLR.
 * Readers deliberately retain the old executable translation while waiting.
 * No guest OS, disk, or display device is needed.
 */
.text
.p2align 4
entry:
    mov x25, x0
    mov x24, x1
    mov x19, #1
start_cpu:
    mov x0, #3
    movk x0, #0xc400, lsl #16 /* PSCI CPU_ON64 */
    mov x1, x19
    adr x2, secondary
    mov x3, #0
    hvc #0
    cbnz x0, fail
    add x19, x19, #1
    cmp x19, #6
    b.ne start_cpu
    b setup
secondary:
    mrs x19, mpidr_el1
    and x19, x19, #255
setup:
    msr daifset, #15
    mrs x23, CurrentEL
    mov x15, #3
    msr cntkctl_el1, x15
    mov x15, #0xff
    msr mair_el1, x15
    mov x15, #0x3519
    movk x15, #2, lsl #32
    msr tcr_el1, x15
    adr x15, table
    msr ttbr0_el1, x15
    adr x15, vectors
    msr vbar_el1, x15
    dsb sy
    isb
    mov x15, #0x1805
    movk x15, #0x30d0, lsl #16
    msr sctlr_el1, x15
    isb
    cmp x19, #6
    b.ne reader
    adr x15, ready
    mov x16, #0x40000000
    add x15, x15, x16
    msr elr_el1, x15
    msr spsr_el1, xzr
    eret
.org 0x100
ready:
    mrs x20, cntvct_el0
    mrs x21, cntfrq_el0
    svc #0
    mrs x22, cntvct_el0
    sub x22, x22, x20
    mov x0, #0
    b done
.org 0x800
vectors:
    b fail
.org 0xc00
    b writer
.org 0x1000
done:
    b done
writer:
    adr x27, leaf
    adr x28, shared
    mov x26, #0x2000
    movk x26, #0xc000, lsl #16
    mov x9, #0
    mov x10, #0
    /* Readers first execute A and acknowledge readiness with -1. */
wait_ready:
    mov x11, #1
ready_loop:
    add x12, x28, x11, lsl #6
    ldar x13, [x12]
    cmn x13, #1
    b.ne wait_ready
    add x11, x11, #1
    cmp x11, #6
    b.ne ready_loop
write_loop:
    add x10, x10, #1
#ifdef SMALL_PAGES
    mov x15, #0x2703
#else
    mov x15, #0x701
#endif
    movk x15, #0x4020, lsl #16
    tbz x10, #0, map_it
    movk x15, #0x4040, lsl #16
map_it:
    /* Break before make: readers wait on the separate phase word. */
    str xzr, [x27]
    dsb ishst
    lsr x16, x26, #12
    /* TG=4KiB. x24 contains the encoded NUM/SCALE test variant. */
    mov x17, #1
    orr x16, x16, x17, lsl #46
    orr x16, x16, x24
#ifndef OMIT_TLBI
    sys #0, c8, c2, #1, x16 /* TLBI RVAE1IS */
#endif
    dsb ish
    str x15, [x27]
    dsb ishst
    isb
    stlr x10, [x28]
    blr x26
    mov x14, #7
    tbz x10, #0, check_writer
    mov x14, #11
check_writer:
    cmp x0, x14
    b.ne fail
    add x9, x9, x0
    mov x11, #1
wait_ack:
    add x12, x28, x11, lsl #6
    ldar x13, [x12]
    cmp x13, x10
    b.ne wait_ack
    add x11, x11, #1
    cmp x11, #6
    b.ne wait_ack
    cmp x10, x25
    b.ne write_loop
    eret
reader:
    adr x28, shared
    add x27, x28, x19, lsl #6
    mov x26, #0x2000
    movk x26, #0xc000, lsl #16
    blr x26
    cmp x0, #7
    b.ne fail
    mov x10, #0
    mov x13, #-1
    stlr x13, [x27]
read_loop:
    ldar x11, [x28]
    cmp x11, x10
    b.eq read_loop
    mov x10, x11
    isb
    blr x26
    mov x14, #7
    tbz x10, #0, check_reader
    mov x14, #11
check_reader:
    cmp x0, x14
    b.ne fail
    stlr x10, [x27]
    b read_loop
fail:
    b fail
.org 0x2000
function_a:
    mov x0, #7
    ret
.org 0x10000
table:
    .quad 0
    .quad 0x40000701
    .quad 0x40000741
    .quad 0x40211003
.org 0x11000
#ifdef SMALL_PAGES
    .quad 0x40213003
.org 0x13010
leaf:
    .quad 0x40202703
#else
leaf:
    .quad 0x40200701
#endif
.org 0x14000
shared:
    .space 384
.org 0x202000
function_b:
    mov x0, #11
    ret
