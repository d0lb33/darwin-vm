/* Independent diskless HVF probe; no project firmware or older probe code.
 * x1 selects arithmetic, Apple register read, GENTER, HVC, or a tiny
 * EL2 exception shim for one explicitly encoded Apple register.
 */
.text
.p2align 12
.globl _native_probe_begin, _native_probe_vectors, _native_probe_end, _native_probe_stop, _native_probe_resume
_native_probe_begin:
    mrs x5, CurrentEL
    adr x15, Lvectors
    cmp x5, #8
    b.ne Lset_vbar1
    msr vbar_el2, x15
    b Lconfigured
Lset_vbar1:
    msr vbar_el1, x15
Lconfigured:
    isb
    mov x2, #0
    mov x3, #0
    mov x7, #0
    mov x12, #0
    cmp x1, #1
    b.eq Lregister
    cmp x1, #2
    b.eq Lgenter
    cmp x1, #3
    b.eq Lhvc
    cmp x1, #4
    b.eq Lshadow
    cmp x1, #5
    b.eq Lvhe
    cmp x1, #6
    b.eq Lagt
    cmp x1, #7
    b.eq Lhcrx
    cmp x1, #8
    b.eq Lhost_shadow
    cmp x1, #9
    b.eq Lpartial_ram
Larithmetic:
    add x2, x2, #3
    subs x0, x0, #1
    b.ne Larithmetic
    b Ldone
Lregister:
    mrs x6, S3_6_C15_C11_1
    b Ldone
Lgenter:
    .long 0x00201420
    b Ldone
Lhvc:
    hvc #0x71
    b Ldone
Lshadow:
    add x3, x3, #1
    msr S3_6_C15_C11_1, x3
    mrs x6, S3_6_C15_C11_1
    cmp x6, x3
    b.ne Lfailed
    add x2, x2, x6
    subs x0, x0, #1
    b.ne Lshadow
    b Ldone
Lvhe:
    mrs x6, ID_AA64MMFR1_EL1
    mrs x0, HCR_EL2
    orr x0, x0, #0x8000000
    orr x0, x0, #0x400000000
    msr HCR_EL2, x0
    isb
    mrs x2, HCR_EL2
    msr VBAR_EL1, x15
    isb
    mrs x3, VBAR_EL2
    b Ldone
Lagt:
    msr S3_1_C15_C9_4, xzr
    b Ldone
Lhcrx:
    mrs x2, S3_4_C1_C2_2
    b Ldone
Lhost_shadow:
    add x3, x3, #1
    smc #0xd103
    smc #0xd126
    cmp x6, x3
    b.ne Lfailed
    add x2, x2, x6
    subs x0, x0, #1
    b.ne Lhost_shadow
    b Ldone
Lpartial_ram:
    /* Darwin's small RAM region spans 0x210050000..0x210059010.
     * Test its final mapped word, unmapped tail, and adjacent MMIO. */
    mov x13, #0x7ff8
    movk x13, #0x1005, lsl #16
    movk x13, #2, lsl #32
    mov x3, #0x1234
    str x3, [x13]
    ldr x6, [x13]
    cmp x3, x6
    b.ne Lfailed
    add x2, x2, x6
    add x13, x13, #8
    str x3, [x13]
    ldr x6, [x13]
    cmp x3, x6
    b.ne Lfailed
    add x2, x2, x6
    add x13, x13, #0x1000
    add x13, x13, #8
    str x3, [x13]
    ldr x6, [x13]
    cmp x3, x6
    b.ne Lfailed
    add x2, x2, x6
    add x13, x13, #8
    /* darwin-unimp starts at zero and remembers writes (not RAZ/WI). */
    ldr x6, [x13]
    cbnz x6, Lfailed
    str x3, [x13]
    ldr x6, [x13]
    cmp x6, x3
    b.ne Lfailed
    b Ldone
Ldone:
    mov x4, #0x600d
#if defined(NATIVE_QEMU_UART) || defined(NATIVE_DARWIN_UART)
#ifdef NATIVE_DARWIN_UART
    add x13, x16, #0x20
#else
    mov x13, #0x09000000
    mov w14, #0x301
    str w14, [x13, #0x30]
#endif
    mov w14, #79
    str w14, [x13]
    mov w14, #75
    str w14, [x13]
    mov w14, #10
    str w14, [x13]
_native_probe_stop:
Lpark:
    b Lpark
#else
_native_probe_stop:
    smc #0x42
    b Ldone
#endif
Lfailed:
    mov x4, #0xbad
    smc #0x43
    b Lfailed
_native_probe_resume:
    mrs x2, HCR_EL2
    mrs x3, VBAR_EL2
    smc #0x44
    b _native_probe_resume
.p2align 11
_native_probe_vectors:
Lvectors:
.rept 16
    b Lexception
    .space 124
.endr
Lexception:
    add x7, x7, #1
    cmp x5, #8
    b.ne Lexception1
    mrs x8, ESR_EL2
    mrs x9, ELR_EL2
    cmp x1, #4
    b.ne Ldone
    /* A deliberately narrow shim: only these two instructions are handled.
     * x8-x12 are reserved by this payload, not a general guest ABI. */
    ldr w10, [x9]
    mov w11, #0xfb23
    movk w11, #0xd51e, lsl #16
    cmp w10, w11
    b.eq Lwrite
    mov w11, #0xfb26
    movk w11, #0xd53e, lsl #16
    cmp w10, w11
    b.ne Lfailed
    mov x6, x12
    b Lreturn
Lwrite:
    mov x12, x3
Lreturn:
    add x9, x9, #4
    msr ELR_EL2, x9
    eret
Lexception1:
    mrs x8, ESR_EL1
    mrs x9, ELR_EL1
    b Ldone
_native_probe_end:
