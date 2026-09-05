/* Exercise the real virtual-EL2 dispatcher, with explicit offset controls. */
.text
.globl _counter_begin, _counter_route, _counter_sample, _counter_done
.globl _counter_write_ss
_counter_begin:
    msr CNTVOFF_EL2, x0
    msr S3_1_C15_C9_4, x1
    mrs x2, CNTVOFF_EL2
    mrs x3, S3_1_C15_C9_4
    mov x8, #0x80000000
    orr x8, x8, #0x400000000
    orr x8, x8, #0x8000000
    msr HCR_EL2, x8
    mov x8, #3
_counter_route:
    msr S3_1_C15_C1_5, x8
    msr S3_4_C15_C14_6, x8
    mrs x4, S3_4_C15_C14_5
    mrs x5, S3_4_C15_C14_6
_counter_sample:
    mrs x10, CNTFRQ_EL0
    mrs x11, CNTVCT_EL0
    mrs x12, S3_3_C14_C0_6
    mrs x13, S3_4_C15_C10_6
    mrs x14, S3_4_C15_C10_5
    mrs x15, CNTPCT_EL0
    cbz x20, Lafter
Lspin:
    subs x20, x20, #1
    b.ne Lspin
Lafter:
    mrs x16, S3_3_C14_C0_6
    mrs x17, S3_4_C15_C10_6
    mrs x18, S3_3_C14_C0_5
    mrs x19, CNTPCT_EL0
_counter_done:
    smc #0xda20
    b _counter_done
_counter_write_ss:
    msr S3_3_C14_C0_6, x0
    b _counter_done
