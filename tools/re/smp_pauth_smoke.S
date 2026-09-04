// Differential mask-cache exercise: randomized TCR, both EL2 regimes,
// lower/upper addresses, instruction/data PAC, AUT and XPAC operations.
// DARWIN_PAUTH_CACHE=verify compares every strip with the original algorithm.
.text
.arch armv8.3-a
start:
    adr x20, start
    add x20, x20, #0x18, lsl #12
    msr daifset, #15
    mov x0, #0x2000
    movk x0, #0xc800, lsl #16
    msr sctlr_el2, x0       // all four PAuth keys enabled, MMU off
    isb
    mov x21, #0            // regime index
    mov x22, #0x1234       // deterministic xorshift state
    mov x23, #0            // total cases
    mov x24, #0            // result checksum
regime:
    mov x0, #0x80000000    // RW, ordinary EL2
    cbz x21, set_hcr
    orr x0, x0, #0x400000000 // E2H
    orr x0, x0, #0x8000000   // TGE
set_hcr:
    msr hcr_el2, x0
    isb
    mov x25, #256
configuration:
    eor x22, x22, x22, lsl #13
    eor x22, x22, x22, lsr #7
    eor x22, x22, x22, lsl #17
    msr tcr_el2, x22       // includes changing TBI/TBID, sizes and granules
    isb
    mov x26, #0
address:
    eor x1, x22, x22, ror #23
    bfi x1, x26, #55, #1   // explicitly exercise each address half
    mov x2, x1
    xpaci x1
    xpacd x2
    eor x24, x24, x1
    eor x24, x24, x2
    mov x3, x22
    mov x4, x22
    autia x3, x26
    autda x4, x26
    eor x24, x24, x3
    eor x24, x24, x4
    mov x3, x22
    mov x4, x22
    autib x3, x26
    autdb x4, x26
    eor x24, x24, x3
    eor x24, x24, x4
    mov x3, x22
    pacia x3, x26
    cmp x3, x22            // preserve the fork's existing signing no-op
    b.ne failed
    mov x3, x22
    pacda x3, x26
    cmp x3, x22
    b.ne failed
    add x23, x23, #1
    add x26, x26, #1
    cmp x26, #2
    b.ne address
    subs x25, x25, #1
    b.ne configuration
    add x21, x21, #1
    cmp x21, #2
    b.ne regime
    str w23, [x20]
    str x24, [x20, #8]
    mov w0, #0x600d
    str w0, [x20, #24]
done:
    wfi
    b done
failed:
    mov w0, #1
    str w0, [x20, #4]
    b done
