/* Counter capability fixture using native_el2_probe.c's isolated VM runner.
 * Cases: ordinary counters, redirection read, redirection write/read,
 * Apple counter read, and architectural self-synchronizing counter read.
 * The first guest exception ends the case; no register fault is skipped.
 */
.text
.p2align 12
.globl _native_probe_begin, _native_probe_vectors, _native_probe_end
.globl _native_probe_resume
_native_probe_begin:
    mrs x5, CurrentEL
    mov x7, #0
    mov x2, #0
    mov x3, #0
    mov x6, #0
    cbz x1, Lordinary
    cmp x1, #3
    b.eq Lapple
    cmp x1, #4
    b.eq Lsynchronized
    cmp x1, #5
    b.eq Lfrequency
    cmp x1, #6
    b.eq Lvirtual
    cmp x1, #7
    b.eq Lphysical
    cmp x5, #8
    b.eq Lredirect2
    cmp x1, #2
    b.ne Lread1
    mov x11, #3
    msr S3_1_C15_C1_5, x11
    isb
Lread1:
    mrs x6, S3_1_C15_C1_5
    b Lordinary
Lredirect2:
    cmp x1, #2
    b.ne Lread2
    mov x11, #3
    msr S3_4_C15_C14_5, x11
    isb
Lread2:
    mrs x6, S3_4_C15_C14_5
    b Lordinary
Lapple:
    mrs x6, S3_4_C15_C10_6
    b Ldone
Lsynchronized:
    mrs x6, S3_3_C14_C0_6
    b Ldone
Lfrequency:
    mrs x6, CNTFRQ_EL0
    b Ldone
Lvirtual:
    mrs x6, CNTVCT_EL0
    b Ldone
Lphysical:
    mrs x6, CNTPCT_EL0
    b Ldone
Lordinary:
    mrs x2, CNTVCT_EL0
    mrs x3, CNTPCT_EL0
    cbnz x1, Ldone
    mrs x6, CNTFRQ_EL0
Ldone:
    mov x4, #0x600d
_native_probe_resume:
    smc #0x42
    b Ldone
.p2align 11
_native_probe_vectors:
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
    b Ldone
Lexception1:
    mrs x8, ESR_EL1
    mrs x9, ELR_EL1
    b Ldone
_native_probe_end:
