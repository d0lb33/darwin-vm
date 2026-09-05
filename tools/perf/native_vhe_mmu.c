/* Diskless high-half VHE translation control, no HCR API access or kernel GIC. */
#include <Hypervisor/Hypervisor.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
extern const unsigned char vmmu_begin[], vmmu_entry[], vmmu_vectors[], vmmu_end[];
#define CHECK(expr) do { hv_return_t r = (expr); if (r) { \
    fprintf(stderr, "%s: 0x%x\n", #expr, r); return 1; } } while (0)
#ifndef VMMU_GPA
#define VMMU_GPA UINT64_C(0x40000000)
#endif
#define GPA VMMU_GPA
#ifdef NATIVE_EL1_MMU
#define NESTED false
#define PSTATE 0x3c5
#define CURRENT_EL 4
#else
#define NESTED true
#define PSTATE 0x3c9
#define CURRENT_EL 8
#endif
#define HIGH UINT64_C(0xffffff8000000000)

int main(int argc, char **argv)
{
    if (argc != 4) return 2;
    uint64_t hcr = strtoull(argv[1], NULL, 0);
    unsigned high = strtoul(argv[2], NULL, 0);
    unsigned mode = strtoul(argv[3], NULL, 0);
    if (high > 1 || mode > 3) return 2;
    alarm(4);
    hv_vm_config_t cfg = hv_vm_config_create();
    CHECK(hv_vm_config_set_el2_enabled(cfg, NESTED));
    CHECK(hv_vm_create(cfg));
    os_release(cfg);
    char *mem = mmap(NULL, 0x40000, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (mem == MAP_FAILED || vmmu_end - vmmu_begin > 0x4000) return 1;
    memcpy(mem, vmmu_begin, vmmu_end - vmmu_begin);
    /* The low and high roots map one VA offset to distinct physical pages. */
    for (unsigned bank = 0; bank < 2; bank++) {
        uint64_t *l1 = (uint64_t *)(mem + 0x4000 + bank * 0xc000);
        uint64_t *l2 = l1 + 0x800, *l3 = l2 + 0x800;
        l1[0] = GPA + 0x8003 + bank * 0xc000;
        l2[GPA >> 25] = GPA + 0xc003 + bank * 0xc000;
        for (unsigned i = 0; i < 16; i++) {
            l3[((GPA >> 14) & 0x7ff) + i] = (GPA + i * 0x4000) | 0x703 |
                    (i ? (UINT64_C(3) << 53) : (UINT64_C(1) << 7));
        }
        l3[((GPA >> 14) & 0x7ff) + 12] = (GPA + 0x30000 + bank * 0x4000) | 0x703 | (UINT64_C(3) << 53);
    }
    *(uint64_t *)(mem + 0x30000) = 0x1234;
    *(uint64_t *)(mem + 0x34000) = 0xabcd;
    CHECK(hv_vm_map(mem, GPA, 0x40000, HV_MEMORY_READ | HV_MEMORY_WRITE | HV_MEMORY_EXEC));
    hv_vcpu_t cpu;
    hv_vcpu_exit_t *reason;
    CHECK(hv_vcpu_create(&cpu, &reason, NULL));
    uint64_t mmfr1_before = 0, mmfr1_after = 0;
    hv_return_t vh_set = HV_SUCCESS;
    if (mode == 3) {
        CHECK(hv_vcpu_get_sys_reg(cpu, HV_SYS_REG_ID_AA64MMFR1_EL1, &mmfr1_before));
        vh_set = hv_vcpu_set_sys_reg(cpu, HV_SYS_REG_ID_AA64MMFR1_EL1,
                                    (mmfr1_before & ~UINT64_C(0xf00)) | 0x100);
        CHECK(hv_vcpu_get_sys_reg(cpu, HV_SYS_REG_ID_AA64MMFR1_EL1, &mmfr1_after));
    }
    if (mode == 1) CHECK(hv_vcpu_set_sys_reg(cpu, HV_SYS_REG_TTBR1_EL2, GPA + 0x10000));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_X27, mode == 3 ? 0 : mode));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_PC, GPA));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_CPSR, PSTATE));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_X0, hcr));
    uint64_t tcr = 25 | 1ull << 8 | 1ull << 10 | 3ull << 12 | 2ull << 14 |
                   25ull << 16 | 1ull << 24 | 1ull << 26 | 3ull << 28 | 1ull << 30 | 1ull << 32;
    uint64_t base = GPA | (high ? HIGH : 0);
    uint64_t regs[] = {GPA + 0x20000, GPA + 0x4000, GPA + 0x10000, tcr,
        GPA + (vmmu_vectors - vmmu_begin), base + (vmmu_entry - vmmu_begin), base + 0x30000};
    for (unsigned i = 0; i < 7; i++) CHECK(hv_vcpu_set_reg(cpu, HV_REG_X20 + i, regs[i]));
    CHECK(hv_vcpu_run(cpu));
    hv_vcpu_exit_t exit = *reason;
    uint64_t *values = (uint64_t *)(mem + 0x20000);
    bool passed = exit.reason == HV_EXIT_REASON_EXCEPTION && exit.exception.syndrome == 0x5e000042 &&
                  values[3] == (NESTED ? hcr : 0) && values[5] == CURRENT_EL && values[4] == (high ? 0xabcd : 0x1234);
    printf("{\"hcr\":\"0x%" PRIx64 "\",\"high\":%s,\"mode\":%u,\"exit_reason\":%u,"
           "\"host_esr\":\"0x%" PRIx64 "\",\"guest_esr\":\"0x%" PRIx64 "\","
           "\"guest_far\":\"0x%" PRIx64 "\",\"guest_elr\":\"0x%" PRIx64 "\","
           "\"guest_hcr\":\"0x%" PRIx64 "\",\"loaded\":\"0x%" PRIx64 "\",\"current_el\":%" PRIu64 ",\"vh_set_result\":%u,\"mmfr1_before\":\"0x%" PRIx64 "\",\"mmfr1_after\":\"0x%" PRIx64 "\",\"passed\":%s}\n",
           hcr, high ? "true" : "false", mode, exit.reason, exit.exception.syndrome,
           values[0], values[1], values[2], values[3], values[4], values[5], vh_set, mmfr1_before, mmfr1_after, passed ? "true" : "false");
    CHECK(hv_vcpu_destroy(cpu));
    CHECK(hv_vm_destroy());
    munmap(mem, 0x40000);
    return passed ? 0 : 1;
}
