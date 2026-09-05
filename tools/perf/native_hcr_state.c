/* Isolate EL2 state preservation from QEMU and from vCPU register APIs. */
#include <Hypervisor/Hypervisor.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
extern const unsigned char state_begin[], state_after[], state_end[];
extern const unsigned char state_read_hcr[], state_write_hcr[];
#define CHECK(expr) do { hv_return_t r = (expr); if (r) { \
    fprintf(stderr, "%s: 0x%x\n", #expr, r); return 1; } } while (0)

int main(int argc, char **argv)
{
    if (argc != 4) return 2;
    unsigned exit_kind = strtoul(argv[1], NULL, 0);
    unsigned mode = strtoul(argv[2], NULL, 0);
    uint64_t hcr = strtoull(argv[3], NULL, 0);
    if (exit_kind > 1 || mode > 8 || (!exit_kind && mode == 3)) return 2;
    alarm(4);
    hv_vm_config_t cfg = hv_vm_config_create();
    CHECK(hv_vm_config_set_el2_enabled(cfg, true));
    CHECK(hv_vm_create(cfg));
    os_release(cfg);
    void *mem = mmap(NULL, 65536, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (mem == MAP_FAILED) return 1;
    memcpy(mem, state_begin, state_end - state_begin);
    CHECK(hv_vm_map(mem, 0x40000000, 65536, HV_MEMORY_READ | HV_MEMORY_WRITE | HV_MEMORY_EXEC));
    hv_vcpu_t cpu;
    hv_vcpu_exit_t *reason;
    CHECK(hv_vcpu_create(&cpu, &reason, NULL));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_PC, 0x40000000));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_CPSR, 0x3c9));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_X0, hcr));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_X1, exit_kind));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_X20, 0x40008000));
    CHECK(hv_vcpu_run(cpu));
    uint64_t first_ec = reason->exception.syndrome >> 26;
    hv_sys_reg_t ids[] = { HV_SYS_REG_HCR_EL2, HV_SYS_REG_TPIDR_EL2,
        HV_SYS_REG_VBAR_EL2, HV_SYS_REG_TTBR0_EL2, HV_SYS_REG_SCTLR_EL2 };
    uint64_t api[5] = {0};
    uint64_t *before = (uint64_t *)((char *)mem + 0x8000);
    uint64_t *after = (uint64_t *)((char *)mem + 0x8100);
    if (mode == 1 || mode == 2) {
        for (unsigned i = 0; i < 5; i++) {
            CHECK(hv_vcpu_get_sys_reg(cpu, ids[i], &api[i]));
        }
        for (unsigned i = 0; i < 5; i++) {
            CHECK(hv_vcpu_set_sys_reg(cpu, ids[i], mode == 1 ? api[i] : before[i]));
        }
    }
    if (mode == 4) CHECK(hv_vcpu_get_sys_reg(cpu, HV_SYS_REG_HCR_EL2, &api[0]));
    if (mode == 5) CHECK(hv_vcpu_set_sys_reg(cpu, HV_SYS_REG_HCR_EL2, before[0]));
    if (mode == 6) {
        for (unsigned i = 1; i < 5; i++) CHECK(hv_vcpu_get_sys_reg(cpu, ids[i], &api[i]));
    }
    if (mode == 7 || mode == 8) {
        uint64_t saved_x9;
        CHECK(hv_vcpu_get_reg(cpu, HV_REG_X9, &saved_x9));
        if (mode == 8) {
            /* Deliberately trigger the API problem, then restore by guest MSR. */
            CHECK(hv_vcpu_get_sys_reg(cpu, HV_SYS_REG_HCR_EL2, &api[0]));
            CHECK(hv_vcpu_set_reg(cpu, HV_REG_X9, before[0]));
        }
        const unsigned char *entry = mode == 7 ? state_read_hcr : state_write_hcr;
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_PC, 0x40000000 + (entry - state_begin)));
        CHECK(hv_vcpu_run(cpu));
        if ((reason->exception.syndrome >> 26) != 23 ||
            (reason->exception.syndrome & 0xffff) != 0x55) return 1;
        CHECK(hv_vcpu_get_reg(cpu, HV_REG_X9, &api[0]));
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_X9, saved_x9));
    }
    if (mode == 3) {
        /* Retry the MMIO instruction after mapping it. No vCPU register API
         * has been called since the first run. */
        CHECK(hv_vm_map((char *)mem + 0xc000, 0x50000000, 0x4000,
                        HV_MEMORY_READ | HV_MEMORY_WRITE));
    } else {
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_PC, 0x40000000 + (state_after - state_begin)));
    }
    CHECK(hv_vcpu_run(cpu));
    printf("{\"exit_kind\":%u,\"mode\":%u,\"first_ec\":%" PRIu64
           ",\"second_ec\":%" PRIu64 ",\"registers\":[", exit_kind, mode,
           first_ec, reason->exception.syndrome >> 26);
    for (unsigned i = 0; i < 5; i++) {
        printf("%s{\"before\":\"0x%" PRIx64 "\",\"api\":\"0x%" PRIx64
               "\",\"after\":\"0x%" PRIx64 "\"}", i ? "," : "", before[i], api[i], after[i]);
    }
    puts("]}");
    CHECK(hv_vcpu_destroy(cpu));
    CHECK(hv_vm_destroy());
    munmap(mem, 65536);
    return 0;
}
