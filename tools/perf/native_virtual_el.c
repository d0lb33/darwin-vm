/* Bounded, diskless virtual EL2-at-EL1 contract experiment.
 * This fixture supports masked AArch64 EL2/EL1/EL0 transitions only. It is not
 * a replacement for QEMU's complete architectural exception implementation.
 */
#include <Hypervisor/Hypervisor.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include "apple-sprr.h"
extern const unsigned char vel_begin[], vel_end[], vel_vectors2[], vel_vectors1[];
extern const unsigned char vel_bridge[], vel_hcr2_native[], vel_hcr1_native[];
extern const unsigned char vel_secret_fault[], vel_after_hvc[], vel_done[], vel_failed[];
extern const unsigned char vel_user_after[], vel_user_after2[], vel_user_priv[], vel_user_data[], vel_illegal_target[];
#define GPA UINT64_C(0x40000000)
#define VA UINT64_C(0xffffff8080000000)
#define SECRET UINT64_C(0x50000000)
#define HCR_TGE (UINT64_C(1) << 27)
#ifdef VEL_TGE
#define TEST_HCR UINT64_C(0x488000000)
#define TEST_DONE_EL 2
#else
#define TEST_HCR UINT64_C(0x480000000)
#define TEST_DONE_EL 1
#endif
#define HCR_E2H (UINT64_C(1) << 34)
#define CHECK(expr) do { hv_return_t r = (expr); if (r) { \
    fprintf(stderr, "%s: 0x%x\n", #expr, r); return 1; } } while (0)
#define FAIL(...) do { fprintf(stderr, __VA_ARGS__); return 1; } while (0)

/* A64 MRS/MSR encoding, independent of an accelerator's enum values. */
enum { R_CURRENTEL = 0xc212, R_HCR2 = 0xe088, R_VBAR1 = 0xc600,
       R_VBAR2 = 0xe600, R_SPSR1 = 0xc200, R_SPSR2 = 0xe200,
       R_ELR1 = 0xc201, R_ELR2 = 0xe201, R_ESR1 = 0xc290, R_ESR2 = 0xe290,
       R_FAR1 = 0xc300, R_FAR2 = 0xe300 };
typedef struct Bank { uint64_t vbar, spsr, elr, esr, sp, far, pperm; } Bank;
typedef struct Context {
    hv_vcpu_t cpu;
    hv_vcpu_exit_t *exit;
    Bank bank[3];
    uint64_t hcr, pstate;
    unsigned el, exits, patched, bridge_calls, sysregs, erets, hvcs, faults, svcs;
    char *mem;
    uint32_t *original;
    size_t code_size;
    bool no_revoke, no_bank_switch, sp0, illegal, no_tge_route;
} Context;

static bool selected(uint16_t reg)
{
    switch (reg) {
    case R_CURRENTEL: case R_HCR2: case R_VBAR1: case R_VBAR2:
    case R_SPSR1: case R_SPSR2: case R_ELR1: case R_ELR2:
    case R_ESR1: case R_ESR2: case R_FAR1: case R_FAR2:
        return true;
    default:
        return false;
    }
}

static bool sysreg_word(uint32_t word)
{
    return (word & 0xffd00000u) == 0xd5100000u && ((word >> 19) & 3) >= 2;
}

static int save_state(Context *c, bool exception)
{
    uint64_t state;
    if (exception) {
        CHECK(hv_vcpu_get_sys_reg(c->cpu, HV_SYS_REG_SPSR_EL1, &state));
    } else {
        CHECK(hv_vcpu_get_reg(c->cpu, HV_REG_CPSR, &state));
    }
    if ((state & 0x10) || (state & 0xe) != (c->el ? 4 : 0)) FAIL("hardware mode mismatch: 0x%" PRIx64 "\n", state);
    c->pstate = (state & ~UINT64_C(15)) | (c->el ? c->el * 4 + (state & 1) : 0);
    CHECK(hv_vcpu_get_sys_reg(c->cpu, HV_SYS_REG_SP_EL0, &c->bank[0].sp));
    if (c->el) CHECK(hv_vcpu_get_sys_reg(c->cpu, HV_SYS_REG_SP_EL1, &c->bank[c->el].sp));
    return 0;
}

static int resume_context(Context *c, unsigned el, uint64_t pstate, uint64_t pc)
{
    if (el > 2 || (pstate & 0x10) || (pstate & 0xe) != el * 4 || (!el && (pstate & 1)) ||
        (pstate & 0x3c0) != 0x3c0) FAIL("unsupported virtual mode 0x%" PRIx64 "\n", pstate);
    /* Virtual EL2/EL1 execute as EL1; virtual EL0 executes as EL0.
     * Revoke before entering lower EL.
     * Single vCPU only: a production implementation needs global coordination. */
    unsigned permissions = arm_sprr_leaf_permissions(c->bank[el].pperm, 0x703, false);
    hv_memory_flags_t flags = (permissions & 1 ? HV_MEMORY_READ : 0) |
                             (permissions & 2 ? HV_MEMORY_WRITE : 0) |
                             (permissions & 4 ? HV_MEMORY_EXEC : 0);
    if (c->el != el) {
        CHECK(hv_vm_protect(SECRET, 0x4000, c->no_revoke ? HV_MEMORY_READ | HV_MEMORY_WRITE : flags));
        CHECK(hv_vm_protect(GPA, 0x4000, el == 2 ? HV_MEMORY_READ | HV_MEMORY_EXEC : 0));
        CHECK(hv_vm_protect(GPA + 0x4000, 0x4000, el ? HV_MEMORY_READ | HV_MEMORY_EXEC : 0));
        CHECK(hv_vm_protect(GPA + 0x28000, 0x4000, el == 2 ? HV_MEMORY_READ | HV_MEMORY_WRITE : 0));
    }
    CHECK(hv_vcpu_set_sys_reg(c->cpu, HV_SYS_REG_SP_EL0, c->bank[c->no_bank_switch ? 2 : 0].sp));
    if (el) CHECK(hv_vcpu_set_sys_reg(c->cpu, HV_SYS_REG_SP_EL1, c->bank[c->no_bank_switch ? 2 : el].sp));
    CHECK(hv_vcpu_set_reg(c->cpu, HV_REG_CPSR, (pstate & ~UINT64_C(15)) | (el ? 4 + (pstate & 1) : 0)));
    CHECK(hv_vcpu_set_reg(c->cpu, HV_REG_PC, pc));
    c->el = el;
    c->pstate = pstate;
    return 0;
}

static unsigned sync_target(Context *c)
{
    if (c->el < 2 && (c->hcr & HCR_TGE) && !c->no_tge_route) return 2;
    return c->el ? c->el : 1;
}

static int take_exception(Context *c, unsigned target, uint64_t esr,
                          uint64_t return_pc, uint64_t far)
{
    Bank *b = &c->bank[target];
    b->spsr = c->pstate;
    b->elr = return_pc;
    b->esr = esr;
    b->far = far;
    uint64_t vector = b->vbar + (target > c->el ? 0x400 : (c->pstate & 1) ? 0x200 : 0);
    uint64_t mode = target * 4 + 1 + 0x3c0;
    /* Baseline PAN semantics: preserve on EL2 entry with TGE=0, set on EL1
     * entry because this fixture's SCTLR_EL1.SPAN is zero. */
    mode |= c->pstate & (UINT64_C(1) << 22);
    if (target == 1 || (target == 2 && (c->hcr & (HCR_E2H | HCR_TGE)) == (HCR_E2H | HCR_TGE))) {
        mode |= UINT64_C(1) << 22;
    }
    return resume_context(c, target, mode, vector);
}

static int dispatch_instruction(Context *c, uint32_t word, uint64_t pc)
{
    if (word == 0xd69f03e0) { /* ERET, model the virtual level transition. */
        Bank *b = &c->bank[c->el];
        unsigned target = (b->spsr >> 2) & 3;
        if (target > c->el) FAIL("unsupported upward ERET in fixture\n");
        c->erets++;
        if (target == 1 && (c->hcr & HCR_TGE)) {
            /* Illegal return: keep EL/SP, restore NZCV/DAIF, set IL and PC.
             * Let the native CPU raise the ensuing illegal-state exception. */
            uint64_t mask = UINT64_C(0xf00003c0);
            uint64_t state = (c->pstate & ~mask) | (b->spsr & mask) | (UINT64_C(1) << 20);
            return resume_context(c, c->el, state, b->elr);
        }
        return resume_context(c, target, b->spsr, b->elr);
    }
    if (!sysreg_word(word)) FAIL("unhandled instruction 0x%08x\n", word);
    unsigned reg = (word >> 5) & 0xffff, rt = word & 31;
    bool read = word & 0x200000;
    if (!selected(reg)) FAIL("unhandled sysreg 0x%x\n", reg);
    unsigned required = (reg & 0x2000) ? 2 : 1;
    if (c->el < required) {
        c->faults++;
        return take_exception(c, sync_target(c), 0x02000000, pc, 0);
    }
    uint64_t value = 0, *storage = NULL;
    /* VHE aliases apply to VBAR/ESR/ELR/SPSR EL1 accesses at virtual EL2. */
    unsigned bank1 = c->el == 2 && (c->hcr & HCR_E2H) ? 2 : 1;
    switch (reg) {
    case R_CURRENTEL:
        if (!read) FAIL("write CurrentEL\n");
        value = c->el * 4;
        break;
    case R_HCR2: storage = &c->hcr; break;
    case R_VBAR1: storage = &c->bank[bank1].vbar; break;
    case R_VBAR2: storage = &c->bank[2].vbar; break;
    case R_SPSR1: storage = &c->bank[bank1].spsr; break;
    case R_SPSR2: storage = &c->bank[2].spsr; break;
    case R_ELR1: storage = &c->bank[bank1].elr; break;
    case R_ELR2: storage = &c->bank[2].elr; break;
    case R_ESR1: storage = &c->bank[bank1].esr; break;
    case R_ESR2: storage = &c->bank[2].esr; break;
    case R_FAR1: storage = &c->bank[bank1].far; break;
    case R_FAR2: storage = &c->bank[2].far; break;
    }
    if (read) {
        if (storage) value = *storage;
        if (rt != 31) CHECK(hv_vcpu_set_reg(c->cpu, HV_REG_X0 + rt, value));
    } else {
        if (rt != 31) CHECK(hv_vcpu_get_reg(c->cpu, HV_REG_X0 + rt, &value));
        if (reg == R_VBAR1 || reg == R_VBAR2) value &= ~UINT64_C(0x7ff);
        if (reg == R_HCR2 && value != TEST_HCR) {
            FAIL("fixture cannot change translation regime through HCR\n");
        }
        *storage = value;
    }
    c->sysregs++;
    return resume_context(c, c->el, c->pstate, pc + 4);
}

static int run(Context *c)
{
    for (unsigned step = 0; step < 128; step++) {
        CHECK(hv_vcpu_run(c->cpu));
        hv_vcpu_exit_t exit = *c->exit;
        c->exits++;
        uint64_t pc;
        CHECK(hv_vcpu_get_reg(c->cpu, HV_REG_PC, &pc));
        if (exit.reason != HV_EXIT_REASON_EXCEPTION) FAIL("unexpected host exit %u\n", exit.reason);
        unsigned ec = exit.exception.syndrome >> 26;
        if (ec == 0x17 && exit.exception.syndrome == 0x5e000042 &&
            pc == VA + (vel_done - vel_begin) && c->el == TEST_DONE_EL) return 0;
        if (ec == 0x17 && (exit.exception.syndrome & 0xfff0) == 0xef00) {
            unsigned slot = exit.exception.syndrome & 15;
            if (save_state(c, true)) return 1;
            unsigned expected_slot = c->el ? ((c->pstate & 1) ? 4 : 0) : 8;
            if (pc != VA + (vel_bridge - vel_begin) + slot * 128 || slot != expected_slot) {
                FAIL("unexpected vector bridge PC 0x%" PRIx64 " slot %u\n", pc, slot);
            }
            c->bridge_calls++;
            uint64_t esr, elr, far;
            CHECK(hv_vcpu_get_sys_reg(c->cpu, HV_SYS_REG_ESR_EL1, &esr));
            CHECK(hv_vcpu_get_sys_reg(c->cpu, HV_SYS_REG_ELR_EL1, &elr));
            CHECK(hv_vcpu_get_sys_reg(c->cpu, HV_SYS_REG_FAR_EL1, &far));
            if ((esr == 0x56000072 || esr == 0x56000073) && c->el == 0) {
                c->svcs++;
                if (take_exception(c, sync_target(c), esr, elr, 0)) return 1;
                continue;
            }
            if (esr == 0x9200000f && c->el == 0 &&
                elr == UINT64_C(0x80000000) + (vel_user_data - vel_begin) && far == VA + 0x30000) {
                c->faults++;
                if (take_exception(c, sync_target(c), esr, elr, far)) return 1;
                continue;
            }
            if (esr == 0x3a000000 && c->el == 2 && c->illegal &&
                elr == VA + (vel_illegal_target - vel_begin) && (c->pstate & (UINT64_C(1) << 20))) {
                c->faults++;
                if (take_exception(c, 2, esr, elr, 0)) return 1;
                continue;
            }
            uint64_t code_base = c->el ? VA : UINT64_C(0x80000000);
            if (esr == 0x02000000 && elr >= code_base && elr - code_base < c->code_size && !(elr & 3)) {
                uint32_t word = c->original[(elr - code_base) / 4];
                if (sysreg_word(word) && selected((word >> 5) & 0xffff)) {
                    if (dispatch_instruction(c, word, elr)) return 1;
                    continue;
                }
            }
            FAIL("unhandled native exception esr=0x%" PRIx64 " elr=0x%" PRIx64 " far=0x%" PRIx64 "\n", esr, elr, far);
        }
        if (save_state(c, false)) return 1;
        if (ec == 0x17 && exit.exception.syndrome == 0x5e00e100) {
            if (pc < VA || pc - VA >= c->code_size || (pc & 3) ||
                *(uint32_t *)(c->mem + pc - VA) != (0xd4000003u | (0xe100u << 5))) {
                FAIL("invalid patched instruction PC\n");
            }
            if (dispatch_instruction(c, c->original[(pc - VA) / 4], pc)) return 1;
        } else if (ec == 0x16 && exit.exception.syndrome == 0x5a000071 && c->el == 1) {
            c->hvcs++;
            /* HVF reports HVC's return address, unlike SMC's instruction PC. */
            if (pc != VA + (vel_after_hvc - vel_begin)) FAIL("HVC PC mismatch\n");
            if (take_exception(c, 2, exit.exception.syndrome, pc, 0)) return 1;
        } else if (ec == 0x24 && c->el == 1 && pc == VA + (vel_secret_fault - vel_begin) &&
                   exit.exception.physical_address == SECRET) {
            c->faults++;
            if (take_exception(c, 1, 0x9600000f, pc, VA + 0x30000)) return 1;
        } else {
            FAIL("unhandled exit ec=%u syndrome=0x%" PRIx64 " pc=0x%" PRIx64 " vel=%u\n",
                 ec, exit.exception.syndrome, pc, c->el);
        }
    }
    FAIL("exit budget exhausted\n");
}

int main(int argc, char **argv)
{
    alarm(5);
    Context c = {0};
    if (argc == 2 && !strcmp(argv[1], "no-revoke")) c.no_revoke = true;
    else if (argc == 2 && !strcmp(argv[1], "no-bank-switch")) c.no_bank_switch = true;
    else if (argc == 2 && !strcmp(argv[1], "sp0")) c.sp0 = true;
    else if (argc == 2 && !strcmp(argv[1], "illegal")) c.illegal = true;
    else if (argc == 2 && !strcmp(argv[1], "illegal-sp0")) c.illegal = c.sp0 = true;
    else if (argc == 2 && !strcmp(argv[1], "no-tge-route")) c.no_tge_route = true;
    else if (argc != 1) return 2;
#ifndef VEL_TGE
    if (c.illegal || c.no_tge_route) return 2;
#endif
    c.code_size = vel_end - vel_begin;
    if (c.code_size > 0x14000 || (c.code_size & 3)) return 1;
    c.original = malloc(c.code_size);
    if (!c.original) return 1;
    memcpy(c.original, vel_begin, c.code_size);
    c.mem = mmap(NULL, 0x34000, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (c.mem == MAP_FAILED) return 1;
    memcpy(c.mem, c.original, c.code_size);
    for (size_t offset = 0; offset < c.code_size; offset += 4) {
        uint32_t word = c.original[offset / 4];
        if (offset == (size_t)(vel_hcr2_native - vel_begin) ||
            offset == (size_t)(vel_hcr1_native - vel_begin)) continue;
        if (word == 0xd69f03e0 || (sysreg_word(word) && selected((word >> 5) & 0xffff))) {
            *(uint32_t *)(c.mem + offset) = 0xd4000003u | (0xe100u << 5);
            c.patched++;
        }
    }
    uint64_t *l1 = (uint64_t *)(c.mem + 0x14000);
    uint64_t *l2 = (uint64_t *)(c.mem + 0x18000);
    uint64_t *l3 = (uint64_t *)(c.mem + 0x1c000);
    l1[0] = GPA + 0x18003;
    l2[(VA >> 25) & 0x7ff] = l2[GPA >> 25] = GPA + 0x1c003;
    for (unsigned i = 0; i < 12; i++) {
        bool executable = i < 4;
        uint64_t attributes = executable ? (UINT64_C(1) << 7) : (UINT64_C(3) << 53);
        if (i == 3) attributes = 0xc0 | (UINT64_C(1) << 53); /* User RX, privileged NX. */
        if (i == 9) attributes |= 0x40; /* User stack RW, NX. */
        l3[i] = (GPA + i * 0x4000) | 0x703 | attributes;
    }
    l3[12] = SECRET | 0x703 | (UINT64_C(3) << 53);
    *(uint64_t *)(c.mem + 0x30000) = 0xface;
    hv_vm_config_t cfg = hv_vm_config_create();
    CHECK(hv_vm_config_set_el2_enabled(cfg, false));
    CHECK(hv_vm_create(cfg));
    os_release(cfg);
    CHECK(hv_vm_map(c.mem, GPA, 0x30000, HV_MEMORY_READ | HV_MEMORY_WRITE | HV_MEMORY_EXEC));
    CHECK(hv_vm_map(c.mem + 0x30000, SECRET, 0x4000, HV_MEMORY_READ | HV_MEMORY_WRITE));
        CHECK(hv_vm_protect(GPA, 0x14000, HV_MEMORY_READ | HV_MEMORY_EXEC));
        CHECK(hv_vm_protect(GPA + 0x14000, 0xc000, HV_MEMORY_READ));
    CHECK(hv_vcpu_create(&c.cpu, &c.exit, NULL));
    uint64_t tcr = 25 | 1ull << 8 | 1ull << 10 | 3ull << 12 | 2ull << 14 |
                   25ull << 16 | 1ull << 24 | 1ull << 26 | 3ull << 28 | 1ull << 30 | 1ull << 32;
    CHECK(hv_vcpu_set_sys_reg(c.cpu, HV_SYS_REG_TTBR0_EL1, GPA + 0x14000));
    CHECK(hv_vcpu_set_sys_reg(c.cpu, HV_SYS_REG_TTBR1_EL1, GPA + 0x14000));
    CHECK(hv_vcpu_set_sys_reg(c.cpu, HV_SYS_REG_TCR_EL1, tcr));
    CHECK(hv_vcpu_set_sys_reg(c.cpu, HV_SYS_REG_MAIR_EL1, 0xff));
    CHECK(hv_vcpu_set_sys_reg(c.cpu, HV_SYS_REG_VBAR_EL1, VA + (vel_bridge - vel_begin)));
    CHECK(hv_vcpu_set_sys_reg(c.cpu, HV_SYS_REG_SCTLR_EL1, 0x1005));
    CHECK(hv_vcpu_set_reg(c.cpu, HV_REG_X14, 0x777));
    CHECK(hv_vcpu_set_reg(c.cpu, HV_REG_X18, c.illegal));
    CHECK(hv_vcpu_set_reg(c.cpu, HV_REG_X19, c.sp0));
    CHECK(hv_vcpu_set_reg(c.cpu, HV_REG_X20, VA + 0x20000));
    CHECK(hv_vcpu_set_reg(c.cpu, HV_REG_X26, VA + 0x30000));
    CHECK(hv_vcpu_set_reg(c.cpu, HV_REG_X28, 0));
    c.hcr = TEST_HCR;
    c.bank[1].vbar = VA + (vel_vectors1 - vel_begin);
    c.bank[0].sp = UINT64_C(0x80027ff0);
    c.bank[1].sp = VA + 0x2fff0;
    c.bank[2].sp = VA + 0x2bff0;
    c.bank[0].pperm = 0;
    c.bank[1].pperm = 0;
    c.bank[2].pperm = 3;
    if (resume_context(&c, 2, 0x3c9, VA)) return 1;
    int result = run(&c);
    uint64_t *v = (uint64_t *)(c.mem + 0x20000);
    uint64_t expected[] = {8, VA + (vel_vectors2 - vel_begin), 0x480000000,
        0xa0000000, 4, VA + 0x2fff0, 0x02000000, VA + (vel_hcr1_native - vel_begin),
        0xa00003c5, 0x9600000f, VA + (vel_secret_fault - vel_begin), 0xa00003c5,
        8, 0x600003c5, VA + (vel_after_hvc - vel_begin), 0x5a000071, VA + 0x2bff0,
        0xface, VA + 0x2fff0, 0x60000000, 1ull << 22, 0,
        0x56000072, UINT64_C(0x80000000) + (vel_user_after - vel_begin), 0x200003c0,
        0x20000000, 0xabe, 0x80027ff0, 0x56000073,
        UINT64_C(0x80000000) + (vel_user_after2 - vel_begin), 0x200003c0,
        0x20000000, 0x80027ff0, 4,
        0x02000000, UINT64_C(0x80000000) + (vel_user_priv - vel_begin), 0x200003c0,
        0x9200000f, UINT64_C(0x80000000) + (vel_user_data - vel_begin), 0x200003c0, VA + 0x30000,
        c.sp0 ? UINT64_C(0x80027ff0) : VA + 0x2bff0, 0, 0, 0, 0, 0, 0};
#ifdef VEL_TGE
    memset(expected, 0, 22 * sizeof(uint64_t));
    expected[0] = 8;
    expected[1] = VA + (vel_vectors2 - vel_begin);
    expected[2] = TEST_HCR;
    expected[20] = 1ull << 22;
    expected[33] = 8;
    if (c.illegal) {
        uint64_t fault[] = {0x3a000000, VA + (vel_illegal_target - vel_begin),
                            c.sp0 ? 0xa01003c8 : 0xa01003c9, 8, VA + 0x2bff0, 0x777};
        memcpy(expected + 42, fault, sizeof(fault));
    }
#endif
    for (unsigned i = 0; i < sizeof(expected) / sizeof(expected[0]); i++) {
        if (v[i] != expected[i]) {
            fprintf(stderr, "result[%u]=0x%" PRIx64 " expected=0x%" PRIx64 "\n", i, v[i], expected[i]);
            result = 1;
        }
    }
#ifdef VEL_TGE
    result |= c.bridge_calls != 5 + (unsigned)c.illegal || c.faults != 2 + (unsigned)c.illegal ||
              c.hvcs != 0 || c.erets != 4 + 2 * (unsigned)c.illegal || c.svcs != 2;
#else
    result |= c.bridge_calls != 6 || c.faults != 4 || c.hvcs != 1 || c.erets != 8 || c.svcs != 2;
#endif
    result |= *(uint64_t *)(c.mem + 0x30000) != 0xface;
    printf("{\"passed\":%s,\"patched\":%u,\"exits\":%u,\"bridge_calls\":%u,"
           "\"sysregs\":%u,\"erets\":%u,\"hvcs\":%u,\"faults\":%u,\"svcs\":%u,\"values\":[",
           result ? "false" : "true", c.patched, c.exits, c.bridge_calls,
           c.sysregs, c.erets, c.hvcs, c.faults, c.svcs);
    for (unsigned i = 0; i < 48; i++) printf("%s\"0x%" PRIx64 "\"", i ? "," : "", v[i]);
    puts("]}");
    CHECK(hv_vcpu_destroy(c.cpu));
    CHECK(hv_vm_destroy());
    munmap(c.mem, 0x34000);
    free(c.original);
    return result;
}
