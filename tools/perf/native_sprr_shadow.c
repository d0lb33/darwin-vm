/* Native enforcement for candidate SPRR shadow leaves. No guest disks.
 * Logical PTEs are separate from shadow PTEs; this is not a production walker.
 */
#include <Hypervisor/Hypervisor.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include "apple-sprr.h"
extern const unsigned char sprr_begin[], sprr_virtual[], sprr_vectors[];
extern const unsigned char sprr_helper[], sprr_end[];
#define CHECK(expr) do { hv_return_t r = (expr); if (r) { \
    fprintf(stderr, "%s: 0x%x\n", #expr, r); return 1; } } while (0)
#define GPA UINT64_C(0x40000000)
#ifndef SPRR_VA
#define SPRR_VA UINT64_C(0x80000000)
#endif
#define VA SPRR_VA
#ifdef NATIVE_SPRR_EL1
#define NESTED false
#define PSTATE 0x3c5
#define FAULT_HCR 0
#define SYSREG(name) HV_SYS_REG_ ## name ## _EL1
#else
#define NESTED true
#define PSTATE 0x3c9
#define FAULT_HCR UINT64_C(0x488000000)
#define SYSREG(name) HV_SYS_REG_ ## name ## _EL2
#endif
#define ALIAS_IPA UINT64_C(0x50000000)

typedef struct Probe {
    hv_vcpu_t cpu;
    hv_vcpu_exit_t *reason;
    char *mem;
    unsigned accesses, denied;
} Probe;

static hv_memory_flags_t memory_flags(unsigned permissions)
{
    return (permissions & ARM_SPRR_READ ? HV_MEMORY_READ : 0) |
           (permissions & ARM_SPRR_WRITE ? HV_MEMORY_WRITE : 0) |
           (permissions & ARM_SPRR_EXEC ? HV_MEMORY_EXEC : 0);
}

/* Reenter the MMU-on guest without touching TTBRs or invalidating its TLB.
 * This also tests hv_vm_protect's revocation after an access was cached. */
static int access_page(Probe *p, unsigned alias, unsigned op, unsigned permissions)
{
    hv_vcpu_t cpu = p->cpu;
    uint64_t target = VA + 0x10000 + alias * 0x4000;
    uint64_t ipa = ALIAS_IPA + alias * 0x4000 + (op == 2 ? 0 : 0x80);
    uint64_t *value = (uint64_t *)(p->mem + 0x20080);
    uint64_t before = *value;
    bool allowed = permissions & (1u << op);
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_X26, target));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_X27, op));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_X4, 0));
    CHECK(hv_vcpu_set_reg(cpu, HV_REG_PC, VA + (sprr_virtual - sprr_begin)));
    CHECK(hv_vcpu_run(cpu));
    hv_vcpu_exit_t access_exit = *p->reason;
    uint64_t result, fault_pc = 0, saved_pstate = 0;
    CHECK(hv_vcpu_get_reg(cpu, HV_REG_X4, &result));
    bool valid = access_exit.reason == HV_EXIT_REASON_EXCEPTION;
    if (allowed) {
        valid &= access_exit.exception.syndrome == 0x5e000042 &&
                 result == (op == 0 ? before : op == 1 ? 0xdef : 0xcafe);
    } else {
        valid &= (access_exit.exception.syndrome >> 26) == (op == 2 ? 0x20 : 0x24);
        valid &= access_exit.exception.physical_address == ipa;
        if (!valid) goto report;
        /* Deliver a stage-1 permission fault, never retry with broader rights.
         * This injection fixture is deliberately EL2h with DAIF masked. */
        CHECK(hv_vcpu_get_reg(cpu, HV_REG_PC, &fault_pc));
        CHECK(hv_vcpu_get_reg(cpu, HV_REG_CPSR, &saved_pstate));
        uint64_t esr = op == 2 ? 0x8600000f : op == 1 ? 0x9600004f : 0x9600000f;
        CHECK(hv_vcpu_set_sys_reg(cpu, SYSREG(ESR), esr));
        CHECK(hv_vcpu_set_sys_reg(cpu, SYSREG(FAR), target + (op == 2 ? 0 : 0x80)));
        CHECK(hv_vcpu_set_sys_reg(cpu, SYSREG(ELR), fault_pc));
        CHECK(hv_vcpu_set_sys_reg(cpu, SYSREG(SPSR), saved_pstate));
#ifndef NATIVE_SPRR_EL1
        uint64_t x9;
        CHECK(hv_vcpu_get_reg(cpu, HV_REG_X9, &x9));
        CHECK(hv_vcpu_set_sys_reg(cpu, SYSREG(SCTLR), 0x1004));
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_X9, 0x488000000));
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_PC, GPA + (sprr_helper - sprr_begin)));
        CHECK(hv_vcpu_run(cpu));
        if (p->reason->reason != HV_EXIT_REASON_EXCEPTION || p->reason->exception.syndrome != 0x5e000055) return 1;
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_X9, x9));
        CHECK(hv_vcpu_set_sys_reg(cpu, SYSREG(SCTLR), 0x1005));
#endif
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_CPSR, (saved_pstate & 0xf0000000) | PSTATE));
        CHECK(hv_vcpu_set_reg(cpu, HV_REG_PC, VA + (sprr_vectors - sprr_begin) + 0x200));
        CHECK(hv_vcpu_run(cpu));
        uint64_t *fault = (uint64_t *)(p->mem + 0x18000);
        valid &= p->reason->reason == HV_EXIT_REASON_EXCEPTION && p->reason->exception.syndrome == 0x5e000077 &&
                 fault[0] == esr && fault[1] == target + (op == 2 ? 0 : 0x80) &&
                 fault[2] == fault_pc && fault[3] == saved_pstate && fault[4] == FAULT_HCR;
        p->denied++;
    }
    valid &= *value == (allowed && op == 1 ? 0xdef : before);
report:
    p->accesses++;
    if (!valid) {
        fprintf(stderr, "access %u alias=%u op=%u permissions=%u reason=%u esr=0x%" PRIx64
                " ipa=0x%" PRIx64 " x4=0x%" PRIx64 " value=0x%" PRIx64 "\n",
                p->accesses, alias, op, permissions, access_exit.reason,
                access_exit.exception.syndrome, access_exit.exception.physical_address, result, *value);
    }
    return !valid;
}

int main(int argc, char **argv)
{
    if (argc != 5) return 2;
    uint64_t pperm = strtoull(argv[1], NULL, 0);
    unsigned index = strtoul(argv[2], NULL, 0);
    unsigned guarded = strtoul(argv[3], NULL, 0);
    unsigned op = strtoul(argv[4], NULL, 0);
    if (index > 15 || guarded > 1 || op > 2) return 2;
    alarm(4);
    uint64_t original = UINT64_C(0x60000000) | 0x703 |
                        ((uint64_t)(index >> 2) << 6) |
                        ((uint64_t)(index & 3) << 53);
    unsigned permissions = arm_sprr_leaf_permissions(pperm, original, guarded);
    hv_vm_config_t cfg = hv_vm_config_create();
    CHECK(hv_vm_config_set_el2_enabled(cfg, NESTED));
    CHECK(hv_vm_create(cfg));
    os_release(cfg);
    char *mem = mmap(NULL, 0x24000, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (mem == MAP_FAILED || sprr_end - sprr_begin > 0x4000) return 1;
    memcpy(mem, sprr_begin, sprr_end - sprr_begin);
    uint64_t *l1 = (uint64_t *)(mem + 0x4000);
    uint64_t *l2 = (uint64_t *)(mem + 0x8000);
    uint64_t *l3 = (uint64_t *)(mem + 0xc000);
    l1[0] = GPA + 0x8003;
    l2[GPA >> 25] = l2[(VA >> 25) & 0x7ff] = GPA + 0xc003;
    for (unsigned i = 0; i < 8; i++) {
        l3[i] = (GPA + i * 0x4000) | 0x703 |
                (i ? (UINT64_C(3) << 53) : (UINT64_C(1) << 7));
    }
    l3[4] = ALIAS_IPA | 0x703;
    l3[5] = (ALIAS_IPA + 0x4000) | 0x703;
    uint32_t *data = (uint32_t *)(mem + 0x20000);
    data[0] = 0xd2995fc4; /* MOV X4, #0xcafe */
    data[1] = 0xd61f0320; /* BR X25 */
    *(uint64_t *)(mem + 0x20080) = 0xabc;
    *(uint64_t *)(mem + 0x1c000) = original;
    CHECK(hv_vm_map(mem, GPA, 0x20000, HV_MEMORY_READ | HV_MEMORY_WRITE | HV_MEMORY_EXEC));
    CHECK(hv_vm_map(data, ALIAS_IPA, 0x4000, memory_flags(permissions)));
    CHECK(hv_vm_map(data, ALIAS_IPA + 0x4000, 0x4000, HV_MEMORY_READ | HV_MEMORY_WRITE));
    Probe p = {.mem = mem};
    CHECK(hv_vcpu_create(&p.cpu, &p.reason, NULL));
    CHECK(hv_vcpu_set_reg(p.cpu, HV_REG_PC, GPA));
    CHECK(hv_vcpu_set_reg(p.cpu, HV_REG_CPSR, PSTATE));
    CHECK(hv_vcpu_set_reg(p.cpu, HV_REG_X0, 0x488000000));
    uint64_t tcr = 25 | 1ull << 8 | 1ull << 10 | 3ull << 12 | 2ull << 14 |
                   25ull << 16 | 1ull << 24 | 1ull << 26 | 3ull << 28 | 1ull << 30 | 1ull << 32;
    uint64_t regs[] = {VA + 0x18000, GPA + 0x4000, tcr, 0xff,
        VA + (sprr_vectors - sprr_begin), VA + (sprr_virtual - sprr_begin)};
    for (unsigned i = 0; i < 6; i++) CHECK(hv_vcpu_set_reg(p.cpu, HV_REG_X20 + i, regs[i]));
    /* Stop just after entering virtual execution, before touching a leaf. */
    CHECK(hv_vcpu_run(p.cpu));
    if (p.reason->reason != HV_EXIT_REASON_EXCEPTION || p.reason->exception.syndrome != 0x5e000041) return 1;
    bool valid = !access_page(&p, 0, op, permissions);
    /* Guest writes through the other alias must be visible through this one,
     * but may not grant extra access. Exercise the second alias's NX fault. */
    if (valid) valid = !access_page(&p, 1, 0, 3);
    if (valid) valid = !access_page(&p, 1, 1, 3);
    if (valid) valid = !access_page(&p, 1, 2, 3);
    if (valid) valid = !access_page(&p, 0, 0, permissions);
    /* Prime the exact access immediately before revoking it. In an allowed
     * case, no fault helper/MMU toggle occurs between this access and protect. */
    if (valid) valid = !access_page(&p, 0, op, permissions);
    CHECK(hv_vm_protect(ALIAS_IPA, 0x4000, 0));
    if (valid) valid = !access_page(&p, 0, op, 0);
    CHECK(hv_vm_protect(ALIAS_IPA, 0x4000, memory_flags(permissions)));
    if (valid) valid = !access_page(&p, 0, op, permissions);
    valid &= *(uint64_t *)(mem + 0x1c000) == original;
    printf("{\"pperm\":\"0x%" PRIx64 "\",\"index\":%u,\"guarded\":%u,\"op\":%u,"
           "\"permissions\":%u,\"accesses\":%u,\"denied\":%u,\"passed\":%s}\n",
           pperm, index, guarded, op, permissions, p.accesses, p.denied, valid ? "true" : "false");
    CHECK(hv_vcpu_destroy(p.cpu));
    CHECK(hv_vm_destroy());
    munmap(mem, 0x24000);
    return valid ? 0 : 1;
}
