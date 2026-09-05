/* Fresh public-Hypervisor.framework capability/exception-routing experiment. */
#include <Hypervisor/Hypervisor.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

extern const unsigned char native_probe_begin[], native_probe_vectors[], native_probe_end[];
extern const unsigned char native_probe_resume[];

static void check(hv_return_t r, const char *call)
{
    if (r != HV_SUCCESS) {
        fprintf(stderr, "%s: 0x%x\n", call, r);
        exit(1);
    }
}
#define CHECK(call) check((call), #call)

static double now(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

int main(int argc, char **argv)
{
    if (argc != 4 && argc != 5 && argc != 6) return 2;
    unsigned el = (unsigned)strtoul(argv[1], NULL, 0);
    unsigned scenario = (unsigned)strtoul(argv[2], NULL, 0);
    uint64_t count = strtoull(argv[3], NULL, 0);
    if ((el != 1 && el != 2) || scenario > 7 || !count || count > 10000000) return 2;
    alarm(4); /* A bad guest cannot keep this owned probe running. */
    bool supported = false;
    CHECK(hv_vm_config_get_el2_supported(&supported));
    if (el == 2 && !supported) {
        puts("{\"el2_supported\":false,\"fallback\":\"tcg\"}");
        return 77;
    }
    hv_vm_config_t config = hv_vm_config_create();
    uint32_t ipa_bits = 0;
    uint64_t ipa = 0x40000000;
    if (argc == 6) {
        uint32_t max_bits;
        CHECK(hv_vm_config_get_max_ipa_size(&max_bits));
        fprintf(stderr, "maximum IPA bits: %u\n", max_bits);
        ipa_bits = (uint32_t)strtoul(argv[4], NULL, 0);
        ipa = strtoull(argv[5], NULL, 0);
        CHECK(hv_vm_config_set_ipa_size(config, ipa_bits));
    }
    CHECK(hv_vm_config_get_ipa_size(config, &ipa_bits));
    CHECK(hv_vm_config_set_el2_enabled(config, el == 2));
    bool enabled = false;
    CHECK(hv_vm_config_get_el2_enabled(config, &enabled));
    CHECK(hv_vm_create(config));
    os_release(config);
    const size_t size = 65536;
    void *mem = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
    if (mem == MAP_FAILED) return 1;
    size_t length = (size_t)(native_probe_end - native_probe_begin);
    if (length > size) return 1;
    memcpy(mem, native_probe_begin, length);
    CHECK(hv_vm_map(mem, ipa, size, HV_MEMORY_READ | HV_MEMORY_WRITE | HV_MEMORY_EXEC));
    hv_vcpu_t cpu;
    hv_vcpu_exit_t *reason;
    CHECK(hv_vcpu_create(&cpu, &reason, NULL));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_PC, ipa));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_CPSR, el == 2 ? 0x3c9 : 0x3c5));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_X0, count));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_X1, scenario));
    CHECK(hv_vcpu_set_sys_reg(cpu, el == 2 ? HV_SYS_REG_VBAR_EL2 : HV_SYS_REG_VBAR_EL1,
                            ipa + (native_probe_vectors - native_probe_begin)));
    double start = now();
    CHECK(hv_vcpu_run(cpu));
    double elapsed = now() - start;
    uint64_t regs[10];
    for (unsigned i = 0; i < 10; i++) CHECK(hv_vcpu_get_reg(cpu, HV_REG_X0 + i, &regs[i]));
    uint64_t pc;
    CHECK(hv_vcpu_get_reg(cpu, HV_REG_PC, &pc));
    uint64_t api_hcr = 0;
    uint64_t host_cntfrq;
    asm volatile("mrs %0, cntfrq_el0" : "=r"(host_cntfrq));
    if (el == 2) CHECK(hv_vcpu_get_sys_reg(cpu, HV_SYS_REG_HCR_EL2, &api_hcr));
    printf("{\"ipa_bits\":%u,\"ipa\":\"0x%" PRIx64 "\","
           "\"host_cntfrq_hz\":%" PRIu64 ",\"exit_syndrome\":\"0x%" PRIx64 "\","
           "\"api_hcr\":\"0x%" PRIx64 "\","
           "\"el2_supported\":%s,\"el2_enabled\":%s,\"el\":%u,\"case\":%u,"
           "\"iterations\":%" PRIu64 ",\"seconds\":%.9f,\"exit_reason\":%u,"
           "\"exit_ec\":%" PRIu64 ",\"pc\":\"0x%" PRIx64 "\","
           "\"checksum\":%" PRIu64 ",\"x3\":%" PRIu64 ",\"done\":%" PRIu64 ",\"current_el\":%" PRIu64 ","
           "\"register_value\":%" PRIu64 ",\"guest_exceptions\":%" PRIu64 ","
           "\"guest_esr\":\"0x%" PRIx64 "\",\"guest_elr\":\"0x%" PRIx64 "\"}\n",
           ipa_bits, ipa, host_cntfrq, reason->exception.syndrome,
           api_hcr, supported ? "true" : "false", enabled ? "true" : "false", el, scenario,
           count, elapsed, reason->reason, reason->exception.syndrome >> 26, pc,
           regs[2], regs[3], regs[4], regs[5], regs[6], regs[7], regs[8], regs[9]);
    if (argc == 5) {
        if (el != 2 || scenario != 5) return 2;
        unsigned mode = (unsigned)strtoul(argv[4], NULL, 0);
        if (mode > 2) return 2;
        if (mode) CHECK(hv_vcpu_set_sys_reg(cpu, HV_SYS_REG_HCR_EL2,
                                          mode == 1 ? api_hcr : regs[2]));
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_PC,
                             ipa + (native_probe_resume - native_probe_begin)));
        CHECK(hv_vcpu_run(cpu));
        uint64_t after, vbar;
        CHECK(hv_vcpu_get_reg(cpu, HV_REG_X2, &after));
        CHECK(hv_vcpu_get_reg(cpu, HV_REG_X3, &vbar));
        printf("{\"roundtrip_mode\":%u,\"before\":\"0x%" PRIx64
               "\",\"after\":\"0x%" PRIx64 "\",\"vbar\":\"0x%" PRIx64 "\"}\n",
               mode, regs[2], after, vbar);
    }
    CHECK(hv_vcpu_destroy(cpu));
    CHECK(hv_vm_unmap(ipa, size));
    CHECK(hv_vm_destroy());
    munmap(mem, size);
    return 0;
}
