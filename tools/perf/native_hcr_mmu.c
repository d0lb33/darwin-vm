/* MMU-on HCR helper and RX/NX preservation, independent of QEMU. */
#include <Hypervisor/Hypervisor.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
extern const unsigned char mmu_begin[], mmu_virtual[], mmu_after[], mmu_vectors[], mmu_end[];
extern const unsigned char mmu_wait[], mmu_done[], mmu_fault_done[];
#ifndef MMU_SHIFT
#define MMU_SHIFT 0
#endif
#define GPA (0x40000000ull + MMU_SHIFT)
#define GVA (0x80000000ull + MMU_SHIFT)
#define CHECK(expr) do { hv_return_t r = (expr); if (r) { \
    fprintf(stderr, "%s: 0x%x\n", #expr, r); return 1; } } while (0)

#ifdef NATIVE_QEMU_MMU
static void *kick(void *opaque)
{
    hv_vcpu_t *cpu = opaque;
    usleep(20000);
    hv_vcpus_exit(cpu, 1);
    return NULL;
}
#endif

static hv_return_t run_guest(hv_vcpu_t cpu)
{
#ifdef NATIVE_QEMU_MMU
    pthread_t thread;
    if (pthread_create(&thread, NULL, kick, &cpu)) return HV_ERROR;
#endif
    hv_return_t r = hv_vcpu_run(cpu);
#ifdef NATIVE_QEMU_MMU
    pthread_join(thread, NULL);
#endif
    return r;
}

static int create_vm(void)
{
    hv_vm_config_t cfg = hv_vm_config_create();
    if (getenv("HVF_PROBE_IPA_BITS")) {
        CHECK(hv_vm_config_set_ipa_size(cfg,
                                       strtoul(getenv("HVF_PROBE_IPA_BITS"), NULL, 0)));
    }
    CHECK(hv_vm_config_set_el2_enabled(cfg, true));
    CHECK(hv_vm_create(cfg));
    os_release(cfg);
    if (getenv("HVF_PROBE_GIC")) {
        hv_gic_config_t gic = hv_gic_config_create();
        CHECK(hv_gic_config_set_distributor_base(gic, 0x08000000));
        CHECK(hv_gic_config_set_redistributor_base(gic, 0x080a0000));
        CHECK(hv_gic_create(gic));
        os_release(gic);
    }
    return 0;
}

static int run_probe(int argc, char **argv)
{
    if (argc < 3 || argc > 5) return 2;
    unsigned mode = strtoul(argv[1], NULL, 0);
    unsigned protection = strtoul(argv[2], NULL, 0);
    if (mode > 2 || protection > 2) return 2;
    alarm(4);
    if (!getenv("HVF_PROBE_SPLIT_THREAD") && create_vm()) return 1;
    size_t ram_size = getenv("HVF_PROBE_RAM_SIZE") ? strtoul(getenv("HVF_PROBE_RAM_SIZE"), NULL, 0) : 0x20000;
    if (ram_size < 0x20000 || ram_size > 0x4000000 || (ram_size & 0x3fff)) return 2;
    char *mem = mmap(NULL, ram_size + 0x4000, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (mem == MAP_FAILED) return 1;
    if (mmu_end - mmu_begin > 0x4000) return 1;
    memcpy(mem, mmu_begin, mmu_end - mmu_begin);
    /* 16KB stage-1 tables. Two VA aliases of the same eight physical pages.
     * Code is RO+X; tables/results are RW+XN. Unmapped leaves stay invalid. */
    uint64_t *l1 = (uint64_t *)(mem + 0x4000);
    uint64_t *l2 = (uint64_t *)(mem + 0x8000);
    uint64_t *l3 = (uint64_t *)(mem + 0xc000);
    l1[0] = (GPA + 0x8003);
    l2[GPA >> 25] = (GPA + 0xc003);
    l2[GVA >> 25] = (GPA + 0xc003);
    for (unsigned i = 0; i < 8; i++) {
        l3[(MMU_SHIFT >> 14) + i] = (GPA + i * 0x4000) | 0x703;
        l3[(MMU_SHIFT >> 14) + i] |= i ? (3ull << 53) : (1ull << 7);
    }
    uint32_t *helper = (uint32_t *)(mem + ram_size);
    helper[0] = 0xd53c1109; /* MRS X9, HCR_EL2 */
    helper[1] = 0xd4000aa3; /* SMC #0x55 */
    helper[2] = 0xd51c1109; /* MSR HCR_EL2, X9 */
    helper[3] = 0xd5033fdf; /* ISB */
    helper[4] = 0xd53c1109;
    helper[5] = 0xd4000aa3;
    CHECK(hv_vm_map(mem, GPA, ram_size, HV_MEMORY_READ | HV_MEMORY_WRITE | HV_MEMORY_EXEC));
    CHECK(hv_vm_map(helper, 0x50000000, 0x4000, HV_MEMORY_READ | HV_MEMORY_EXEC));
    if (getenv("HVF_PROBE_LOW_ROM")) {
        void *rom = mmap(NULL, 0x8000000, PROT_READ | PROT_WRITE,
                         MAP_PRIVATE | MAP_ANON, -1, 0);
        if (rom == MAP_FAILED) return 1;
        CHECK(hv_vm_map(rom, 0, 0x8000000, HV_MEMORY_READ | HV_MEMORY_EXEC));
    }
    hv_vcpu_t cpu;
    hv_vcpu_exit_t *reason;
    CHECK(hv_vcpu_create(&cpu, &reason, NULL));
    unsigned pre_reg = argc >= 4 ? strtoul(argv[3], NULL, 0) : 0;
    if (argc == 5) {
        FILE *state = fopen(argv[4], "r");
        unsigned reg;
        uint64_t value;
        if (!state) return 1;
        while (fscanf(state, "%x %" SCNx64, &reg, &value) == 2) {
            if (pre_reg == 7 && reg == HV_SYS_REG_HCR_EL2) {
                CHECK(hv_vcpu_set_reg(cpu, HV_REG_CPSR, 0x3c9));
                CHECK(hv_vcpu_set_reg(cpu, HV_REG_PC, 0x50000008));
                CHECK(hv_vcpu_set_reg(cpu, HV_REG_X9, value));
                CHECK(hv_vcpu_run(cpu));
                if (reason->exception.syndrome != 0x5e000055) return 1;
            } else {
                CHECK(hv_vcpu_set_sys_reg(cpu, reg, value));
            }
        }
        fclose(state);
    }
    if (pre_reg && pre_reg < 4) {
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_CPSR, 0x3c9));
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_PC, 0x50000008));
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_X9,
                            pre_reg == 1 ? 0 : pre_reg == 2 ? 0x80000000 : 0x488000000));
        CHECK(hv_vcpu_run(cpu));
        if (reason->exception.syndrome != 0x5e000055) return 1;
    } else if (pre_reg == 4) {
        CHECK(hv_vcpu_set_vtimer_offset(cpu, 1000000000));
    } else if (pre_reg == 5 || pre_reg == 6) {
        CHECK(hv_vcpu_set_trap_debug_exceptions(cpu, pre_reg == 5));
        CHECK(hv_vcpu_set_trap_debug_reg_accesses(cpu, pre_reg == 5));
    } else if (pre_reg == 8) {
        uint64_t v;
        CHECK(hv_vcpu_get_reg(cpu, HV_REG_CPSR, &v));
        CHECK(hv_vcpu_get_reg(cpu, HV_REG_PC, &v));
        CHECK(hv_vcpu_get_reg(cpu, HV_REG_X9, &v));
    } else if (pre_reg == 9) {
        hv_simd_fp_uchar16_t fp = {0};
        for (unsigned i = 0; i < 32; i++) {
            CHECK(hv_vcpu_set_simd_fp_reg(cpu, HV_SIMD_FP_REG_Q0 + i, fp));
        }
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_FPCR, 0));
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_FPSR, 0));
    } else if (pre_reg && pre_reg != 7) {
        uint64_t initial;
        hv_sys_reg_t reg = pre_reg & 0xffff;
        CHECK(hv_vcpu_get_sys_reg(cpu, reg, &initial));
        if (pre_reg & 0x10000) CHECK(hv_vcpu_set_sys_reg(cpu, reg, initial));
        if (pre_reg & 0x20000) CHECK(hv_vcpu_set_sys_reg(cpu, reg, 0));
    }
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_PC, GPA));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_CPSR, 0x3c9));
    uint64_t tcr = 25 | (1ull << 8) | (1ull << 10) | (3ull << 12) |
                   (2ull << 14) | (25ull << 16) | (1ull << 30) | (1ull << 32);
    uint64_t values[] = {(GVA + 0x10000), (GPA + 0x4000), tcr, 0xff,
        GVA + (mmu_vectors - mmu_begin),
        GVA + (mmu_virtual - mmu_begin), protection};
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_X0, 0x488000000));
    for (unsigned i = 0; i < 7; i++) CHECK(hv_vcpu_set_reg(cpu, HV_REG_X20 + i, values[i]));
    CHECK(run_guest(cpu));
    uint64_t first = reason->exception.syndrome;
    uint64_t *before = (uint64_t *)(mem + 0x10000);
    uint64_t *after = (uint64_t *)(mem + 0x10100);
    uint64_t captured = before[0];
#ifdef NATIVE_QEMU_MMU
    bool first_ok = reason->reason == HV_EXIT_REASON_CANCELED;
#else
    bool first_ok = (first >> 26) == 23 && (first & 0xffff) == 0x41;
#endif
    if (!first_ok) {
        fprintf(stderr, "unexpected first exit: 0x%" PRIx64 "\n", first);
        return 1;
    }
    if (mode) {
        uint64_t saved_x9, sctlr, pstate;
        CHECK(hv_vcpu_get_reg(cpu, HV_REG_X9, &saved_x9));
        CHECK(hv_vcpu_get_reg(cpu, HV_REG_CPSR, &pstate));
        CHECK(hv_vcpu_get_sys_reg(cpu, HV_SYS_REG_SCTLR_EL2, &sctlr));
        CHECK(hv_vcpu_set_sys_reg(cpu, HV_SYS_REG_SCTLR_EL2, sctlr & ~1ull));
        if (mode == 2) {
            uint64_t bad;
            CHECK(hv_vcpu_get_sys_reg(cpu, HV_SYS_REG_HCR_EL2, &bad));
            CHECK(hv_vcpu_set_reg(cpu, HV_REG_X9, before[0]));
        }
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_PC, mode == 1 ? 0x50000000 : 0x50000008));
        CHECK(hv_vcpu_run(cpu));
        if ((reason->exception.syndrome >> 26) != 23 ||
            (reason->exception.syndrome & 0xffff) != 0x55) return 1;
        CHECK(hv_vcpu_get_reg(cpu, HV_REG_X9, &captured));
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_X9, saved_x9));
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_CPSR, pstate));
        CHECK(hv_vcpu_set_sys_reg(cpu, HV_SYS_REG_SCTLR_EL2, sctlr));
    }
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_PC, GVA + (mmu_after - mmu_begin)));
    CHECK(run_guest(cpu));
    uint64_t last = reason->exception.syndrome;
    int preserved = memcmp(before, after, 6 * sizeof(uint64_t)) == 0;
    int code_preserved = memcmp(mem, mmu_begin, mmu_end - mmu_begin) == 0;
    uint64_t early_hcr, mmfr1;
    CHECK(hv_vcpu_get_reg(cpu, HV_REG_X27, &early_hcr));
    CHECK(hv_vcpu_get_reg(cpu, HV_REG_X6, &mmfr1));
    printf("{\"mode\":%u,\"protection\":%u,\"pre_reg\":%u,\"early_hcr\":\"0x%" PRIx64
           "\",\"mmfr1\":\"0x%" PRIx64 "\",\"captured_hcr\":\"0x%" PRIx64
           "\",\"preserved\":%s,\"code_preserved\":%s,\"last_exit\":\"0x%" PRIx64
           "\",\"guest_esr\":\"0x%" PRIx64 "\",\"guest_far\":\"0x%" PRIx64 "\",\"fault_hcr\":\"0x%" PRIx64 "\"}\n",
           mode, protection, pre_reg, early_hcr, mmfr1, captured, preserved ? "true" : "false",
           code_preserved ? "true" : "false", last, after[8], after[9], after[10]);
    unsigned fault_ec = protection == 1 ? 0x25 : 0x21;
#ifdef NATIVE_QEMU_MMU
    uint64_t pc;
    CHECK(hv_vcpu_get_reg(cpu, HV_REG_PC, &pc));
    bool last_ok = reason->reason == HV_EXIT_REASON_CANCELED &&
        pc == GVA + ((protection ? mmu_fault_done : mmu_done) - mmu_begin);
#else
    bool last_ok = (last >> 26) == 23 && (last & 0xffff) == (protection ? 0x77 : 0x42);
#endif
    int valid = preserved && code_preserved && captured == 0x488000000 && last_ok &&
        (!protection || ((after[8] >> 26) == fault_ec && (after[8] & 0x3f) == 0xf && after[10] == 0x488000000));
    CHECK(hv_vcpu_destroy(cpu));
    CHECK(hv_vm_destroy());
    munmap(mem, ram_size + 0x4000);
    return valid ? 0 : 1;
}

struct probe_args { int argc; char **argv; int result; };
static void *probe_thread(void *opaque)
{
    struct probe_args *args = opaque;
    args->result = run_probe(args->argc, args->argv);
    return NULL;
}

int main(int argc, char **argv)
{
    if (!getenv("HVF_PROBE_SPLIT_THREAD")) return run_probe(argc, argv);
    if (create_vm()) return 1;
    struct probe_args args = {argc, argv, 1};
    pthread_t thread;
    if (pthread_create(&thread, NULL, probe_thread, &args)) return 1;
    pthread_join(thread, NULL);
    return args.result;
}
