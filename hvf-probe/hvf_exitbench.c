/*
 * hvf_exitbench.c - how much does one Hypervisor.framework VM exit cost?
 *
 * The HVF-acceleration study (docs/re/hvf-acceleration.md) turns on a cost per
 * VM exit. hvf-probe/FINDINGS.md establishes *which* guest events would exit;
 * this measures *how expensive* one is on the machine in front of us, so the
 * arithmetic uses a measured number instead of a guessed "1-3 us".
 *
 * Four measurements, all with a guest vCPU at EL1 in a plain VM (MMU off, code
 * fetched straight out of the hv_vm_map'd region):
 *
 *   native      a pure register loop with no exits, to get the guest's native
 *               instruction rate under HVF. Reference for "how fast could the
 *               emulator go if the CPU ran natively".
 *   hvc-bare    hv_vcpu_run() returning on an HVC, re-entered with no register
 *               access at all. Lower bound on the exit+entry round trip.
 *   hvc-pcadv   same plus get PC / set PC, i.e. the minimum a VMM must do to
 *               make forward progress.
 *   mrs-full    MRS from an Apple IMP-DEF register (SPRR_CONFIG_EL1,
 *               S3_6_c15_c1_0), handled the way the real thing would: decode
 *               Rt out of the syndrome, write the destination register,
 *               advance PC. This is the exact per-event cost of the 1.5M
 *               Apple sysreg accesses a darwin-vm boot performs.
 *
 * Build and run:  make exitbench && ./hvf_exitbench 500000
 * (needs the com.apple.security.hypervisor entitlement; ad-hoc signing is
 * enough, so no sudo and no Apple developer account)
 *
 * Encodings below were cross-checked against clang's assembler:
 *   d4000002 hvc #0 | d53ef100 mrs x0, S3_6_C15_C1_0 | 17ffffff b .-4
 *   d2a1e001 mov x1, #0x0f000000 | f1000421 subs x1,x1,#1 | 54ffffe1 b.ne .-4
 */
#include <Hypervisor/Hypervisor.h>
#include <mach/mach_time.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <libkern/OSCacheControl.h>

#define GUEST_BASE   0x40000000ULL
#define GUEST_SIZE   0x10000ULL          /* 64 KiB */

#define OFF_HVC      0x0000
#define OFF_MRS      0x0100
#define OFF_NATIVE   0x0200
#define OFF_VBAR     0x1000            /* vector table, 0x800 bytes         */
#define OFF_HANDLER  0x1800            /* our EL1 sync handler              */
#define OFF_UNDEF    0x1900            /* the udf loop the handler services */

#define I_HVC        0xD4000002u         /* hvc #0                      */
#define I_MRS_SPRR   0xD53EF100u         /* mrs x0, S3_6_C15_C1_0       */
#define I_B_BACK4    0x17FFFFFFu         /* b .-4                       */
#define I_MOVZ_X1    0xD2A1E001u         /* mov x1, #0x0f000000         */
#define I_SUBS_X1_1  0xF1000421u         /* subs x1, x1, #1             */
#define I_BNE_BACK4  0x54FFFFE1u         /* b.ne .-4                    */

#define NATIVE_ITERS 0x0F000000ULL       /* must match I_MOVZ_X1        */

/* in-guest exception path: udf -> EL1 vector -> handler -> eret, no VM exit */
#define I_MRS_ELR_X11 0xD538402Bu        /* mrs  x11, elr_el1           */
#define I_ADD_X11_4   0x9100116Bu        /* add  x11, x11, #4           */
#define I_MSR_ELR_X11 0xD518402Bu        /* msr  elr_el1, x11           */
#define I_BEQ_P8      0x54000040u        /* b.eq .+8                    */
#define I_ERET        0xD69F03E0u        /* eret                        */
#define I_UDF         0x00000000u        /* udf #0                      */
#define I_MOVZ_X1_1M  0xD2A00201u        /* mov  x1, #0x100000          */

#define UNDEF_ITERS   0x100000ULL        /* must match I_MOVZ_X1_1M     */

static uint8_t *gmem;
static hv_vcpu_t vcpu;
static hv_vcpu_exit_t *vexit;

#define CHECK(x) do { hv_return_t _r = (x); if (_r != HV_SUCCESS) {          \
    fprintf(stderr, "%s:%d: %s -> 0x%x\n", __FILE__, __LINE__, #x,           \
            (unsigned)_r); exit(1); } } while (0)

static double now_s(void)
{
    static mach_timebase_info_data_t tb;
    if (tb.denom == 0) {
        mach_timebase_info(&tb);
    }
    return (double)mach_absolute_time() * tb.numer / tb.denom / 1e9;
}

static void put(uint64_t off, uint32_t insn)
{
    memcpy(gmem + off, &insn, 4);
}

static void enter_at(uint64_t off)
{
    CHECK(hv_vcpu_set_reg(vcpu, HV_REG_PC, GUEST_BASE + off));
    CHECK(hv_vcpu_set_reg(vcpu, HV_REG_CPSR, 0x3c5));   /* EL1h, DAIF masked */
}

/* Run once and report where HVF leaves PC, so the benchmark below knows
 * whether it must advance PC itself. */
static void probe_convention(void)
{
    enter_at(OFF_HVC);
    CHECK(hv_vcpu_run(vcpu));
    uint64_t pc = 0, esr = vexit->exception.syndrome;
    CHECK(hv_vcpu_get_reg(vcpu, HV_REG_PC, &pc));
    printf("convention: after HVC at 0x%llx, exit reason=%u ESR=0x%llx "
           "EC=0x%x PC=0x%llx (%s)\n",
           (unsigned long long)(GUEST_BASE + OFF_HVC), vexit->reason,
           (unsigned long long)esr, (unsigned)(esr >> 26),
           (unsigned long long)pc,
           pc == GUEST_BASE + OFF_HVC ? "PC left on the HVC"
                                      : "PC already advanced");

    enter_at(OFF_MRS);
    CHECK(hv_vcpu_run(vcpu));
    esr = vexit->exception.syndrome;
    CHECK(hv_vcpu_get_reg(vcpu, HV_REG_PC, &pc));
    printf("convention: after MRS at 0x%llx, exit reason=%u ESR=0x%llx "
           "EC=0x%x Rt=%u PC=0x%llx (%s)\n",
           (unsigned long long)(GUEST_BASE + OFF_MRS), vexit->reason,
           (unsigned long long)esr, (unsigned)(esr >> 26),
           (unsigned)((esr >> 5) & 0x1f),
           (unsigned long long)pc,
           pc == GUEST_BASE + OFF_MRS ? "PC left on the MRS"
                                      : "PC already advanced");
}

static void bench_native(void)
{
    double t0, t1;

    enter_at(OFF_NATIVE);
    t0 = now_s();
    for (;;) {
        CHECK(hv_vcpu_run(vcpu));
        if (vexit->reason == HV_EXIT_REASON_VTIMER_ACTIVATED) {
            hv_vcpu_set_vtimer_mask(vcpu, true);
            continue;
        }
        break;                          /* the trailing HVC */
    }
    t1 = now_s();

    /* movz + (subs + b.ne) * N + hvc */
    double insns = 1.0 + 2.0 * (double)NATIVE_ITERS + 1.0;
    printf("native    : %.6f s for %.0f guest instructions "
           "= %.1f M insn/s (no exits)\n",
           t1 - t0, insns, insns / (t1 - t0) / 1e6);
}

/*
 * The alternative architecture: instead of exiting to the VMM, let a shim
 * inside the guest handle the trap. This measures the floor for that -- an
 * exception taken and returned entirely inside the guest, no VM exit. Under
 * nested virt with HCR_EL2.TIDCP=1 an Apple IMP-DEF access from guest EL1
 * traps to guest EL2 the same way (hvf-probe/results.txt, mode "EL1 under
 * guest EL2 ... TIDCP=1": 7 x "GUEST-EXC@EL2 ESR=0x62.. EC=0x18"), so this is
 * the right order of magnitude for a guest-EL2 shim. Measured here as
 * EL1 -> EL1 because a plain VM needs no nested-virt setup.
 */
static void bench_inguest(void)
{
    double t0, t1;

    /* Enter with the loop counter preloaded; the handler decrements it. */
    CHECK(hv_vcpu_set_sys_reg(vcpu, HV_SYS_REG_VBAR_EL1, GUEST_BASE + OFF_VBAR));
    enter_at(OFF_UNDEF);
    CHECK(hv_vcpu_set_reg(vcpu, HV_REG_X1, UNDEF_ITERS));

    t0 = now_s();
    for (;;) {
        CHECK(hv_vcpu_run(vcpu));
        if (vexit->reason == HV_EXIT_REASON_VTIMER_ACTIVATED) {
            hv_vcpu_set_vtimer_mask(vcpu, true);
            continue;
        }
        break;                          /* the HVC the handler falls into */
    }
    t1 = now_s();

    printf("in-guest  : %.6f s for %llu EL1 exceptions taken and returned "
           "inside the guest = %.0f ns each (no VM exit)\n",
           t1 - t0, (unsigned long long)UNDEF_ITERS,
           (t1 - t0) / (double)UNDEF_ITERS * 1e9);
}

static void bench_exits(const char *label, uint64_t off, int mode, uint64_t n)
{
    double t0, t1;

    enter_at(off);
    /* one warm-up run so the first-entry cost is not in the sample */
    CHECK(hv_vcpu_run(vcpu));

    t0 = now_s();
    for (uint64_t i = 0; i < n; i++) {
        CHECK(hv_vcpu_run(vcpu));
        if (mode >= 1) {
            uint64_t pc = 0;
            CHECK(hv_vcpu_get_reg(vcpu, HV_REG_PC, &pc));
            if (pc == GUEST_BASE + off) {
                CHECK(hv_vcpu_set_reg(vcpu, HV_REG_PC, pc + 4));
            }
        }
        if (mode >= 2) {
            uint64_t esr = vexit->exception.syndrome;
            unsigned rt = (esr >> 5) & 0x1f;
            if (rt != 31) {
                CHECK(hv_vcpu_set_reg(vcpu, HV_REG_X0 + rt, 0x1234));
            }
        }
    }
    t1 = now_s();

    printf("%-10s: %.6f s for %llu exits = %.0f ns per exit "
           "(%.2f M exits/s)\n",
           label, t1 - t0, (unsigned long long)n,
           (t1 - t0) / (double)n * 1e9, (double)n / (t1 - t0) / 1e6);
}

int main(int argc, char **argv)
{
    uint64_t n = 200000;
    if (argc > 1) {
        n = strtoull(argv[1], NULL, 0);
    }

    hv_vm_config_t cfg = hv_vm_config_create();
    CHECK(hv_vm_create(cfg));

    gmem = mmap(NULL, GUEST_SIZE, PROT_READ | PROT_WRITE,
                MAP_ANON | MAP_PRIVATE, -1, 0);
    if (gmem == MAP_FAILED) {
        perror("mmap");
        return 1;
    }
    CHECK(hv_vm_map(gmem, GUEST_BASE, GUEST_SIZE,
                    HV_MEMORY_READ | HV_MEMORY_WRITE | HV_MEMORY_EXEC));
    CHECK(hv_vcpu_create(&vcpu, &vexit, NULL));

    put(OFF_HVC + 0, I_HVC);
    put(OFF_HVC + 4, I_B_BACK4);

    put(OFF_MRS + 0, I_MRS_SPRR);
    put(OFF_MRS + 4, I_B_BACK4);

    put(OFF_NATIVE + 0,  I_MOVZ_X1);
    put(OFF_NATIVE + 4,  I_SUBS_X1_1);
    put(OFF_NATIVE + 8,  I_BNE_BACK4);
    put(OFF_NATIVE + 12, I_HVC);

    /* Every vector slot branches to the one handler. Sync-from-current-EL
     * with SP_ELx is VBAR+0x200; fill all 16 so a stray exception is visible
     * as a hang rather than executing garbage. */
    for (int i = 0; i < 16; i++) {
        uint64_t slot = OFF_VBAR + i * 0x80;
        int64_t rel = (int64_t)OFF_HANDLER - (int64_t)slot;
        put(slot, 0x14000000u | (uint32_t)((rel / 4) & 0x03FFFFFF));
    }
    put(OFF_HANDLER + 0,  I_MRS_ELR_X11);
    put(OFF_HANDLER + 4,  I_ADD_X11_4);
    put(OFF_HANDLER + 8,  I_MSR_ELR_X11);
    put(OFF_HANDLER + 12, I_SUBS_X1_1);
    put(OFF_HANDLER + 16, I_BEQ_P8);
    put(OFF_HANDLER + 20, I_ERET);
    put(OFF_HANDLER + 24, I_HVC);

    put(OFF_UNDEF + 0, I_UDF);
    put(OFF_UNDEF + 4, I_B_BACK4);

    sys_icache_invalidate(gmem, GUEST_SIZE);

    printf("hvf_exitbench: guest EL1, plain VM (no EL2), MMU off; %llu exits "
           "per sample\n", (unsigned long long)n);
    probe_convention();
    bench_native();
    bench_exits("hvc-bare",  OFF_HVC, 0, n);
    bench_exits("hvc-pcadv", OFF_HVC, 1, n);
    bench_exits("mrs-full",  OFF_MRS, 2, n);
    bench_inguest();

    hv_vcpu_destroy(vcpu);
    hv_vm_unmap(GUEST_BASE, GUEST_SIZE);
    hv_vm_destroy();
    return 0;
}
