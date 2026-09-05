/* Public-HVF VHE register-alias test, with no QEMU or project firmware. */
#include <Hypervisor/Hypervisor.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
extern const unsigned char alias_begin[], alias_end[];
#define CHECK(expr) do { hv_return_t r = (expr); if (r) { \
    fprintf(stderr, "%s: 0x%x\n", #expr, r); return 1; } } while (0)
int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    uint64_t hcr = strtoull(argv[1], NULL, 0);
    alarm(4);
    hv_vm_config_t cfg = hv_vm_config_create();
    CHECK(hv_vm_config_set_el2_enabled(cfg, true));
    CHECK(hv_vm_create(cfg));
    os_release(cfg);
    char *mem = mmap(NULL, 0x10000, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANON, -1, 0);
    if (mem == MAP_FAILED || alias_end - alias_begin > 0x1000) return 1;
    memcpy(mem, alias_begin, alias_end - alias_begin);
    CHECK(hv_vm_map(mem, 0x40000000, 0x10000,
                    HV_MEMORY_READ | HV_MEMORY_WRITE | HV_MEMORY_EXEC));
    hv_vcpu_t cpu;
    hv_vcpu_exit_t *reason;
    CHECK(hv_vcpu_create(&cpu, &reason, NULL));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_CPSR, 0x3c9));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_PC, 0x40000000));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_X0, hcr));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_X1, 0x40004000));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_X2, 0x40008000));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_X3, 0x4000c000));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_X20, 0x40003000));
    CHECK(hv_vcpu_run(cpu));
    const uint64_t *v = (uint64_t *)(mem + 0x3000);
    bool valid = reason->reason == HV_EXIT_REASON_EXCEPTION &&
                 reason->exception.syndrome == 0x5e000041;
    bool aliases = v[4] == 0x4000c000;
    bool expected_alias = hcr & (1ull << 34);
    printf("{\"hcr\":\"0x%" PRIx64 "\",\"exit_ok\":%s,\"values\":[",
           hcr, valid ? "true" : "false");
    for (unsigned i = 0; i < 7; i++) printf("%s\"0x%" PRIx64 "\"", i ? "," : "", v[i]);
    printf("],\"aliases\":%s,\"expected_alias\":%s}\n",
           aliases ? "true" : "false", expected_alias ? "true" : "false");
    valid &= v[0] == 0x40004000 && v[1] == 0x40008000 &&
             v[2] == hcr && v[3] == 0x4000c000 && v[5] == hcr &&
             aliases == expected_alias;
    CHECK(hv_vcpu_destroy(cpu));
    CHECK(hv_vm_destroy());
    munmap(mem, 0x10000);
    return valid ? 0 : 1;
}
