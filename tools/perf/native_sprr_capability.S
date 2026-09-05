/* Native capability observations only: never skip an IMP-DEF register fault.
 * Runs in an isolated 64 KiB VM via native_el2_probe.c, with the MMU off.
 * x2=0 before an operation, 1 after a write, 2 after successful readback.
 */
.text
.p2align 12
.globl _native_probe_begin, _native_probe_vectors, _native_probe_end
.globl _native_probe_resume
_native_probe_begin:
    mrs x5, CurrentEL
    mov x2, #0
    mov x3, #0
    mov x4, #0
    mov x6, #0
    mov x7, #0
    mov x11, #0x10
    movk x11, #0x4, lsl #16
    cbz x1, Lread1
    cmp x1, #1
    b.eq Lwrite1
    cmp x1, #2
    b.eq Lread2
    cmp x1, #3
    b.eq Lwrite2
    cmp x1, #4
    b.eq Lconfig1
    cmp x1, #5
    b.eq Lconfig2
    cmp x1, #6
    b.eq Lread12
    mrs x6, CurrentEL
    b Lcomplete
Lwrite1:
    msr S3_6_C15_C3_1, x11
    mov x2, #1
    isb
Lread1:
    mrs x6, S3_6_C15_C3_1
    b Lcomplete
Lwrite2:
    msr S3_6_C15_C3_2, x11
    mov x2, #1
    isb
Lread2:
    mrs x6, S3_6_C15_C3_2
    b Lcomplete
Lconfig1:
    mov x11, #1
    msr S3_6_C15_C1_0, x11
    mov x2, #1
    isb
    mrs x6, S3_6_C15_C1_0
    b Lcomplete
Lconfig2:
    mov x11, #1
    msr S3_6_C15_C14_2, x11
    mov x2, #1
    isb
    mrs x6, S3_6_C15_C14_2
    b Lcomplete
Lread12:
    mrs x6, S3_6_C15_C6_0
Lcomplete:
    mov x2, #2
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
