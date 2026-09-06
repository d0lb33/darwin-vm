/* Synthetic benchmark at the existing pre-SPRR SPTM fixture boundary.
 * x19=count, x20=case; x9 is the checked result. No disk or OS work.
 * Setup inputs match native_virtual_gxf.py. All privileged instructions
 * are ledger-adapted for HVF; TCG executes the original words.
 */
.text
    msr S3_6_C15_C1_0, x0
    msr S3_6_C15_C1_6, x9
    msr S3_6_C15_C3_1, x5
    msr S3_6_C15_C1_0, x6
    msr S3_6_C15_C1_2, x0
    msr S3_6_C15_C8_1, x10
    msr tpidr_el2, x12
    msr tpidr_el0, x12          /* never ledgered: native on both backends */
    b ready
.org 0x100
ready:
    mov x9, #0
    mov x11, #3
    cbz x19, done
    cmp x20, #1
    b.eq neon
    cmp x20, #2
    b.eq sysread
    cmp x20, #3
    b.eq transition
    cmp x20, #4
    b.eq nativeread
    cmp x20, #5
    b.eq guardedread
integer:
    add x9, x9, x11
    add x9, x9, x11
    add x9, x9, x11
    add x9, x9, x11
    add x9, x9, x11
    add x9, x9, x11
    add x9, x9, x11
    add x9, x9, x11
    subs x19, x19, #1
    b.ne integer
    b done
neon:
    movi v0.2d, #0
    dup v1.2d, x11
vector_loop:
    add v0.2d, v0.2d, v1.2d
    add v0.2d, v0.2d, v1.2d
    add v0.2d, v0.2d, v1.2d
    add v0.2d, v0.2d, v1.2d
    add v0.2d, v0.2d, v1.2d
    add v0.2d, v0.2d, v1.2d
    add v0.2d, v0.2d, v1.2d
    add v0.2d, v0.2d, v1.2d
    subs x19, x19, #1
    b.ne vector_loop
    umov x9, v0.d[0]
    b done
sysread:
    mrs x11, tpidr_el2
    add x9, x9, x11
    subs x19, x19, #1
    b.ne sysread
    b done
transition:
    .long 0x00201420
    subs x19, x19, #1
    b.ne transition
    b done
nativeread:
    mrs x11, tpidr_el0
    add x9, x9, x11
    subs x19, x19, #1
    b.ne nativeread
    b done
guardedread:
    /* One GENTER; the guarded handler sets TPIDR_GL2 and loops on it. */
    mov x13, x19
    .long 0x00201420
    b done
.org 0x200
done:
    b done
.org 0x300
guarded:
    cmp x20, #5
    b.eq guarded_loop_entry
    add x9, x9, #1
    .long 0x00201400
guarded_loop_entry:
    msr S3_6_C15_C11_1, x12
    mov x19, x13
guarded_loop:
    mrs x11, S3_6_C15_C11_1
    add x9, x9, x11
    subs x19, x19, #1
    b.ne guarded_loop
    .long 0x00201400
