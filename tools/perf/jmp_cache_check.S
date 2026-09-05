/* Real guest translation-block invalidation checks. No iOS disk is opened.
 * Enter EL0 for the existing harness, then use an EL1 SVC handler to remap
 * one executable VA between two physical pages and change its instructions.
 * Every access remains checked through the guest MMU and architectural TLBI.
 */
.text
.p2align 4
entry:
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
    adr x15, ready
    mov x16, #0x40000000
    add x15, x15, x16
    msr elr_el1, x15
    msr spsr_el1, xzr
    eret
.org 0x100
ready:
    mov x25, x0
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
    b handler
.org 0x1000
done:
    b done
handler:
    mov x9, #0
    mov x26, #0x2000
    movk x26, #0xc000, lsl #16
    adr x27, leaf
check_loop:
    mov x15, #0x701
    movk x15, #0x4020, lsl #16
    str x15, [x27]
    dsb ishst
    lsr x16, x26, #12
    tlbi vae1, x16
    dsb ish
    isb
    blr x26
    cmp x0, #7
    b.ne fail
    add x9, x9, x0

    /* Reset B's code before remapping the executable alias to it. */
    mov x17, #0x2000
    movk x17, #0x4040, lsl #16
    mov w18, #0x160
    movk w18, #0xd280, lsl #16 /* mov x0, #11 */
    str w18, [x17]
    dc cvau, x17
    dsb ish
    ic ivau, x17
    dsb ish
    isb
    mov x15, #0x701
    movk x15, #0x4040, lsl #16
    str x15, [x27]
    dsb ishst
    tlbi vae1, x16
    dsb ish
    isb
    blr x26
    cmp x0, #11
    b.ne fail
    add x9, x9, x0

    /* Change instructions behind an already executed VA. */
    mov w18, #0x1a0
    movk w18, #0xd280, lsl #16 /* mov x0, #13 */
    str w18, [x17]
    dc cvau, x17
    dsb ish
    ic ivau, x26
    dsb ish
    isb
    blr x26
    cmp x0, #13
    b.ne fail
    add x9, x9, x0
    subs x25, x25, #1
    b.ne check_loop
    eret
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
leaf:
    .quad 0x40200701
.org 0x202000
function_b:
    mov x0, #11
    ret
