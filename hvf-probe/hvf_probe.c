/*
 * hvf_probe.c -- minimal standalone Hypervisor.framework probe (no QEMU).
 *
 * Boots a few hand-assembled AArch64 instructions in a guest at EL1 (plain
 * VM) and, when the host supports it, at EL2 and at EL1-under-guest-EL2
 * (EL2-enabled "nested virtualization" VM).  For every probed system
 * register / instruction it records whether the access
 *
 *   - executes natively (value returned, no exit),
 *   - traps to the VMM (HV_EXIT_REASON_EXCEPTION, with the ESR),
 *   - raises an exception inside the guest (caught by a tiny guest vector
 *     table which reports ESR/ELR back to the VMM and skips the instruction),
 *   - or makes Hypervisor.framework itself abort the process (its userspace
 *     emulator has report_fixme_if_and_trap() paths that SIGTRAP).
 *
 * Guest <-> VMM signalling uses a "doorbell": an unmapped IPA region.  Every
 * `str xN, [x9, #off]` there is a stage-2 data abort that exits to the VMM,
 * which decodes the ISS (SRT register, offset) and resumes at pc+4.
 *
 * Every probe "unit" (one register, or one dependent sequence such as the
 * GXF or MTE experiment) runs in a forked child with its own VM so that a
 * framework abort only loses that unit.
 *
 * Build: make      Run: ./hvf_probe [--verbose] [--no-el2] [--modes=12345]
 *        make check  (diff hand-encoded words against clang's assembler)
 */
#include <Hypervisor/Hypervisor.h>
#include <inttypes.h>
#include <libkern/OSCacheControl.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* ---------------------------------------------------------------- layout */
#define GUEST_BASE   0x10000000ULL
#define GUEST_SIZE   (2u << 20)
#define VBAR1_OFF    0x8000u      /* EL1 vector table + handler              */
#define VBAR2_OFF    0xA000u      /* EL2 vector table + handler              */
#define GXF_OFF      0xC000u      /* GXF (genter) entry stub                 */
#define PT_OFF       0x10000u     /* stage-1 level-1 table: 4 x 1GiB blocks  */
#define S2PT_OFF     0x11000u     /* guest stage-2 level-1 table (identity)  */
#define DATA_OFF     0x20000u     /* scratch page for the MTE tag test       */
#define STACK_TOP    (GUEST_BASE + 0x3F000ULL)
#define TESTS_OFF    0x40000u     /* per-test code regions, never reused     */
#define TEST_STRIDE  0x4000u
#define DOORBELL     0x20000000ULL /* unmapped IPA => every store exits      */
#define DB_SIZE      0x10000ULL

#define DB_EXC1_ESR  0x20
#define DB_EXC1_ELR  0x28
#define DB_EXC2_ESR  0x30
#define DB_EXC2_ELR  0x38
#define DB_GXF_HIT   0x40
#define DB_DONE      0x48
#define DB_SLOT0     0x100

#define SENTINEL     0xA5A5A5A5A5A5A5A5ULL

#define SCTLR_EL1_RES1 0x30D00800ULL
#define SCTLR_EL2_RES1 0x30C50830ULL
#define SCTLR_M_C_I    0x1005ULL
#define SCTLR_ATA      ((1ULL << 43) | (1ULL << 42))
/* T0SZ=32, IRGN0/ORGN0=WB, SH0=inner, TG0=4K, EPD1=1, IPS=40bit */
#define TCR_EL1_VAL    (0x20ULL | (1ULL << 8) | (1ULL << 10) | (3ULL << 12) | (1ULL << 23) | (2ULL << 32))
/* attr0 = 0xF0 Tagged Normal WB, attr1 = 0xFF Normal WB, attr2 = Device */
#define MAIR_EL1_VAL   0x0000FFF0ULL
#define HCR_VM         (1ULL << 0)
#define HCR_TIDCP      (1ULL << 20)
#define HCR_RW         (1ULL << 31)
#define HCR_ATA        (1ULL << 56)
#define CPTR_EL2_RES1  0x33FFULL
/* T0SZ=32, SL0=1 (start level 1), IRGN/ORGN=WB, SH=inner, TG0=4K, PS=40bit */
#define VTCR_EL2_VAL   (0x20ULL | (1ULL << 6) | (1ULL << 8) | (1ULL << 10) | (3ULL << 12) | (2ULL << 16))

#define GENTER 0x00201420u
#define GEXIT  0x00201400u
#define ERET   0xD69F03E0u
#define ISB    0xD5033FDFu
#define NOP    0xD503201Fu
#define WFI    0xD503207Fu
#define SVC0   0xD4000001u
#define HVC0   0xD4000002u
#define SMC0   0xD4000003u
#define BRK0   0xD4200000u

static bool verbose, no_el2, try_force_mte;
static const char *modes = "12345";

/* ------------------------------------------------------------- helpers */
static const char *hv_err(hv_return_t r)
{
    switch ((uint32_t)r) {
    case 0:          return "HV_SUCCESS";
    case 0xfae94001: return "HV_ERROR";
    case 0xfae94002: return "HV_BUSY";
    case 0xfae94003: return "HV_BAD_ARGUMENT";
    case 0xfae94004: return "HV_ILLEGAL_GUEST_STATE";
    case 0xfae94005: return "HV_NO_RESOURCES";
    case 0xfae94006: return "HV_NO_DEVICE";
    case 0xfae94007: return "HV_DENIED";
    case 0xfae9400f: return "HV_UNSUPPORTED";
    default:         return "?";
    }
}
#define CHECK(x) do { hv_return_t _r = (x); if (_r != HV_SUCCESS) { \
    fprintf(stderr, "%s:%d: %s -> 0x%x (%s)\n", __FILE__, __LINE__, #x, (unsigned)_r, hv_err(_r)); exit(1); } } while (0)

static uint64_t now_ns(void)
{
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}
static unsigned fld(uint64_t v, int lo, int w) { return (unsigned)((v >> lo) & ((1ULL << w) - 1)); }

static const char *ec_name(unsigned ec)
{
    switch (ec) {
    case 0x00: return "UNKNOWN/UNDEF";
    case 0x01: return "WFx";
    case 0x07: return "FP/SIMD access";
    case 0x0e: return "illegal state";
    case 0x15: return "SVC64";
    case 0x16: return "HVC64";
    case 0x17: return "SMC64";
    case 0x18: return "MSR/MRS/SYS trap";
    case 0x19: return "SVE access";
    case 0x1d: return "SME access";
    case 0x20: return "insn abort (lower EL)";
    case 0x21: return "insn abort (same EL)";
    case 0x22: return "PC alignment";
    case 0x24: return "data abort (lower EL)";
    case 0x25: return "data abort (same EL)";
    case 0x2f: return "SError";
    case 0x3c: return "BRK";
    default:   return "?";
    }
}

/* ------------------------------------------------------- sysreg table */
typedef struct { const char *name; uint8_t op0, op1, crn, crm, op2; } sr_t;
#define SR(n, a, b, c, d, e) { n, a, b, c, d, e }

static const sr_t
    R_CURRENTEL = SR("CurrentEL",        3, 0, 4, 2, 2),
    R_MIDR      = SR("MIDR_EL1",         3, 0, 0, 0, 0),
    R_PFR0      = SR("ID_AA64PFR0_EL1",  3, 0, 0, 4, 0),
    R_PFR1      = SR("ID_AA64PFR1_EL1",  3, 0, 0, 4, 1),
    R_MMFR1     = SR("ID_AA64MMFR1_EL1", 3, 0, 0, 7, 1),
    R_ISAR1     = SR("ID_AA64ISAR1_EL1", 3, 0, 0, 6, 1),
    R_SCTLR_EL1 = SR("SCTLR_EL1",        3, 0, 1, 0, 0),
    R_GCR_EL1   = SR("GCR_EL1",          3, 0, 1, 0, 6),
    R_ESR_EL1   = SR("ESR_EL1",          3, 0, 5, 2, 0),
    R_ELR_EL1   = SR("ELR_EL1",          3, 0, 4, 0, 1),
    R_HCR_EL2   = SR("HCR_EL2",          3, 4, 1, 1, 0),
    R_VTTBR_EL2 = SR("VTTBR_EL2",        3, 4, 2, 1, 0),
    R_SCTLR_EL2 = SR("SCTLR_EL2",        3, 4, 1, 0, 0),
    R_ESR_EL2   = SR("ESR_EL2",          3, 4, 5, 2, 0),
    R_ELR_EL2   = SR("ELR_EL2",          3, 4, 4, 0, 1);

/* Apple IMPLEMENTATION DEFINED registers.  Encodings are the ones the
 * darwin-vm fork emulates (qemu-sptm/build/hw/arm/apple_regs_autogen.h,
 * generated by scripts/darwin/dumpregs.py from sysregs.py). */
static const sr_t
    A_SPRR_CONFIG_EL1  = SR("SPRR_CONFIG_EL1",  3, 6, 15, 1, 0),
    A_SPRR_PPERM_EL1   = SR("SPRR_PPERM_EL1",   3, 6, 15, 1, 6),
    A_SPRR_UPERM_EL0   = SR("SPRR_UPERM_EL0",   3, 6, 15, 1, 5),
    A_SPRR_AMRANGE_EL1 = SR("SPRR_AMRANGE_EL1", 3, 6, 15, 1, 3),
    A_SPRR_PMPRR_EL1   = SR("SPRR_PMPRR_EL1",   3, 6, 15, 3, 1),
    A_SPRR_UMPRR_EL1   = SR("SPRR_UMPRR_EL1",   3, 6, 15, 3, 0),
    A_GXF_CONFIG_EL1   = SR("GXF_CONFIG_EL1",   3, 6, 15, 1, 2),
    A_GXF_ENTRY_EL1    = SR("GXF_ENTRY_EL1",    3, 6, 15, 8, 1),
    A_GXF_PABENTRY_EL1 = SR("GXF_PABENTRY_EL1", 3, 6, 15, 8, 2),
    A_CURRENTG         = SR("CURRENTG",         3, 6, 15, 8, 0),
    A_ASPSR_GL1        = SR("ASPSR_GL1",        3, 6, 15, 10, 4),
    A_SP_GL1           = SR("SP_GL1",           3, 6, 15, 10, 0),
    A_ESR_GL1          = SR("ESR_GL1",          3, 6, 15, 10, 5),
    A_ELR_GL1          = SR("ELR_GL1",          3, 6, 15, 10, 6),
    A_VBAR_GL1         = SR("VBAR_GL1",         3, 6, 15, 10, 2),
    A_APCTL_EL1        = SR("APCTL_EL1",        3, 4, 15, 0, 4),
    A_APSTS_EL1        = SR("APSTS_EL1",        3, 6, 15, 12, 4),
    A_KERNKEYLO_EL1    = SR("KERNKEYLO_EL1",    3, 4, 15, 1, 0),
    A_AMXIDR_EL1       = SR("AMXIDR_EL1",       3, 6, 15, 2, 7),
    A_AMX_CONFIG_EL1   = SR("AMX_CONFIG_EL1",   3, 4, 15, 1, 4),
    A_AMX_STATE_T_EL1  = SR("AMX_STATE_T_EL1",  3, 4, 15, 1, 3),
    A_VMSA_LOCK_EL1    = SR("VMSA_LOCK_EL1",    3, 4, 15, 1, 2),
    A_CTRR_A_LWR_EL1   = SR("CTRR_A_LWR_EL1",   3, 4, 15, 2, 3),
    A_CTRR_A_UPR_EL1   = SR("CTRR_A_UPR_EL1",   3, 4, 15, 2, 4),
    A_CTRR_A_CTL_EL1   = SR("CTRR_A_CTL_EL1",   3, 4, 15, 2, 5),
    A_HID0             = SR("HID0",             3, 0, 15, 0, 0),
    A_HID4             = SR("HID4",             3, 0, 15, 4, 0),
    A_HID11            = SR("HID11",            3, 0, 15, 11, 0),
    A_EHID0            = SR("EHID0",            3, 0, 15, 0, 1),
    A_PMCR0_EL1        = SR("PMCR0_EL1",        3, 1, 15, 0, 0),
    A_PMC0             = SR("PMC0",             3, 2, 15, 0, 0),
    A_CYC_OVRD         = SR("CYC_OVRD",         3, 5, 15, 5, 0),
    A_ACC_CFG          = SR("ACC_CFG",          3, 5, 15, 4, 0),
    A_IPI_SR           = SR("IPI_SR",           3, 5, 15, 1, 1),
    A_IMP_MSR_RO_CTRL0 = SR("IMP_MSR_RO_CTRL0_EL1", 3, 4, 15, 0, 5),
    A_ACNTPCT_EL0      = SR("ACNTPCT_EL0",      3, 4, 15, 10, 5),
    A_ACNTVCT_EL0      = SR("ACNTVCT_EL0",      3, 4, 15, 10, 6),
    /* EL2 variants */
    A_SPRR_CONFIG_EL2  = SR("SPRR_CONFIG_EL2",  3, 6, 15, 14, 2),
    A_SPRR_PPERM_EL2   = SR("SPRR_PPERM_EL2",   3, 6, 15, 1, 7),
    A_SPRR_AMRANGE_EL2 = SR("SPRR_AMRANGE_EL2", 3, 6, 15, 14, 3),
    A_GXF_CONFIG_EL2   = SR("GXF_CONFIG_EL2",   3, 6, 15, 1, 4),
    A_GXF_ENTRY_EL2    = SR("GXF_ENTRY_EL2",    3, 6, 15, 12, 0),
    A_GXF_PABENTRY_EL2 = SR("GXF_PABENTRY_EL2", 3, 6, 15, 12, 1),
    A_SP_GL2           = SR("SP_GL2",           3, 6, 15, 11, 0),
    A_ESR_GL2          = SR("ESR_GL2",          3, 6, 15, 11, 5),
    A_ELR_GL2          = SR("ELR_GL2",          3, 6, 15, 11, 6),
    A_TAG_OFFSET_EL2   = SR("TAG_OFFSET_EL2",   3, 0, 11, 9, 0),
    A_APCTL_EL2        = SR("APCTL_EL2",        3, 6, 15, 12, 2),
    A_APSTS_EL2        = SR("APSTS_EL2",        3, 6, 15, 12, 3),
    A_VMSA_LOCK_EL2    = SR("VMSA_LOCK_EL2",    3, 4, 15, 1, 5),
    A_AMX_CONFIG_EL2   = SR("AMX_CONFIG_EL2",   3, 4, 15, 4, 7),
    A_CTRR_A_LWR_EL2   = SR("CTRR_A_LWR_EL2",   3, 4, 15, 6, 4),
    A_CTRR_A_CTL_EL2   = SR("CTRR_A_CTL_EL2",   3, 4, 15, 6, 2),
    A_AHCR_EL2         = SR("AHCR_EL2",         3, 4, 15, 12, 1),
    A_MMU_SFAR_EL2     = SR("MMU_SFAR_EL2",     3, 6, 15, 14, 6),
    A_HPFAR_GL2        = SR("HPFAR_GL2",        3, 6, 15, 1, 1);

static void sr_enc(const sr_t *r, char *buf, size_t n)
{
    snprintf(buf, n, "S%d_%d_C%d_C%d_%d", r->op0, r->op1, r->crn, r->crm, r->op2);
}

/* ------------------------------------------------------- mini assembler */
static uint8_t  *gmem;           /* host view of guest RAM */
static uint32_t *cur; static uint64_t cur_base; static uint32_t cur_n;
static uint32_t code_off, next_code_off;

static void asm_begin(uint32_t off) { cur = (uint32_t *)(gmem + off); cur_base = GUEST_BASE + off; cur_n = 0; }
static uint64_t here(void) { return cur_base + cur_n * 4u; }
static void emit(uint32_t w) { cur[cur_n++] = w; }

static uint32_t enc_sys(const sr_t *r)
{
    return ((uint32_t)(r->op0 & 1) << 19) | ((uint32_t)r->op1 << 16) | ((uint32_t)r->crn << 12) |
           ((uint32_t)r->crm << 8) | ((uint32_t)r->op2 << 5);
}
static uint32_t enc_MRS(int rt, const sr_t *r) { return 0xD5300000u | enc_sys(r) | (uint32_t)rt; }
static uint32_t enc_MSR(const sr_t *r, int rt) { return 0xD5100000u | enc_sys(r) | (uint32_t)rt; }
static uint32_t enc_MOVZ(int rd, unsigned imm16, int sh) { return 0xD2800000u | ((uint32_t)(sh / 16) << 21) | ((uint32_t)imm16 << 5) | (uint32_t)rd; }
static uint32_t enc_MOVK(int rd, unsigned imm16, int sh) { return 0xF2800000u | ((uint32_t)(sh / 16) << 21) | ((uint32_t)imm16 << 5) | (uint32_t)rd; }
static uint32_t enc_STR(int rt, int rn, unsigned off) { return 0xF9000000u | ((uint32_t)(off >> 3) << 10) | ((uint32_t)rn << 5) | (uint32_t)rt; }
static uint32_t enc_LDR(int rt, int rn, unsigned off) { return 0xF9400000u | ((uint32_t)(off >> 3) << 10) | ((uint32_t)rn << 5) | (uint32_t)rt; }
static uint32_t enc_ADDI(int rd, int rn, unsigned imm) { return 0x91000000u | ((uint32_t)imm << 10) | ((uint32_t)rn << 5) | (uint32_t)rd; }
static uint32_t enc_ORR(int rd, int rn, int rm) { return 0xAA000000u | ((uint32_t)rm << 16) | ((uint32_t)rn << 5) | (uint32_t)rd; }
static uint32_t enc_MOVR(int rd, int rm) { return enc_ORR(rd, 31, rm); }
static uint32_t enc_B(int64_t rel) { return 0x14000000u | ((uint32_t)(rel >> 2) & 0x3FFFFFFu); }
static uint32_t enc_IRG(int rd, int rn, int rm) { return 0x9AC01000u | ((uint32_t)rm << 16) | ((uint32_t)rn << 5) | (uint32_t)rd; }
static uint32_t enc_ADDG(int rd, int rn, unsigned u6, unsigned u4) { return 0x91800000u | ((uint32_t)u6 << 16) | ((uint32_t)u4 << 10) | ((uint32_t)rn << 5) | (uint32_t)rd; }
static uint32_t enc_STG(int rt, int rn) { return 0xD9200800u | ((uint32_t)rn << 5) | (uint32_t)rt; }
static uint32_t enc_LDG(int rt, int rn) { return 0xD9600000u | ((uint32_t)rn << 5) | (uint32_t)rt; }
static uint32_t enc_UBFX(int rd, int rn, unsigned lsb, unsigned width) { return 0xD3400000u | (lsb << 16) | ((lsb + width - 1) << 10) | ((uint32_t)rn << 5) | (uint32_t)rd; }
static uint32_t enc_CMPI(int rn, unsigned imm) { return 0xF100001Fu | (imm << 10) | ((uint32_t)rn << 5); }
static uint32_t enc_BCOND(unsigned cond, int64_t rel) { return 0x54000000u | (((uint32_t)(rel >> 2) & 0x7FFFFu) << 5) | cond; }

static void MRS(int rt, const sr_t *r) { emit(enc_MRS(rt, r)); }
static void MSR(const sr_t *r, int rt) { emit(enc_MSR(r, rt)); }
static void MOV64(int rd, uint64_t v)
{
    emit(enc_MOVZ(rd, v & 0xFFFF, 0));
    emit(enc_MOVK(rd, (v >> 16) & 0xFFFF, 16));
    emit(enc_MOVK(rd, (v >> 32) & 0xFFFF, 32));
    emit(enc_MOVK(rd, (v >> 48) & 0xFFFF, 48));
}
static void STR(int rt, int rn, unsigned off) { emit(enc_STR(rt, rn, off)); }
static void ADDI(int rd, int rn, unsigned imm) { emit(enc_ADDI(rd, rn, imm)); }
static void ORR(int rd, int rn, int rm) { emit(enc_ORR(rd, rn, rm)); }
static void MOVR(int rd, int rm) { emit(enc_MOVR(rd, rm)); }

static void selftest(void)
{
    /* Must match check.S line for line. */
    uint32_t w[] = {
        enc_MRS(0, &A_SPRR_CONFIG_EL1), enc_MSR(&A_SPRR_CONFIG_EL1, 1), enc_MRS(0, &R_CURRENTEL),
        enc_MRS(0, &A_TAG_OFFSET_EL2), enc_MSR(&R_GCR_EL1, 1),
        enc_MOVZ(0, 0x1234, 0), enc_MOVK(0, 0x5678, 16), enc_MOVK(0, 0x9abc, 32), enc_MOVK(0, 0xdef0, 48),
        enc_STR(10, 9, 0x20), enc_LDR(3, 1, 8), enc_ADDI(11, 11, 4), enc_ORR(1, 1, 0), enc_MOVR(3, 1),
        enc_B(0x800), ERET, ISB, NOP, WFI, SVC0, HVC0, SMC0, BRK0,
        enc_IRG(0, 1, 31), enc_ADDG(2, 1, 1, 3), enc_STG(2, 2), enc_LDG(3, 3),
        enc_MRS(10, &R_ESR_EL1), enc_MRS(11, &R_ELR_EL1), enc_MSR(&R_ELR_EL1, 11),
        enc_MRS(10, &R_ESR_EL2), enc_MRS(11, &R_ELR_EL2), enc_MSR(&R_ELR_EL2, 11),
        GENTER, GEXIT,
        enc_UBFX(12, 10, 26, 6), enc_CMPI(12, 0x15), enc_BCOND(0, 24),
    };
    for (size_t i = 0; i < sizeof w / sizeof w[0]; i++) printf("%08x\n", w[i]);
}

/* ------------------------------------------------------- probes/events */
enum { EV_VMMTRAP, EV_EXC1, EV_EXC2, EV_HVC, EV_SMC, EV_OTHER, EV_GXF, EV_FATAL, EV_TIMEOUT };
typedef struct { int type; uint64_t pc, esr, far; char msg[96]; } ev_t;
#define MAX_EV 256
static ev_t events[MAX_EV]; static int nevents;
static uint64_t slot_val[128]; static bool slot_set[128]; static int nslots;

typedef struct { const char *label; char enc[24]; const char *op; uint64_t addr; int slot; } probe_t;
#define MAX_PROBES 160
static probe_t probes[MAX_PROBES]; static int nprobes;

static void push_ev(int type, uint64_t pc, uint64_t esr, uint64_t far, const char *msg)
{
    if (nevents >= MAX_EV) return;
    ev_t *e = &events[nevents++];
    e->type = type; e->pc = pc; e->esr = esr; e->far = far;
    snprintf(e->msg, sizeof e->msg, "%s", msg ? msg : "");
}
static probe_t *add_probe(const char *label, const sr_t *r, const char *op, int slot)
{
    probe_t *p = &probes[nprobes++];
    p->label = label; p->op = op; p->addr = here(); p->slot = slot;
    if (r) sr_enc(r, p->enc, sizeof p->enc); else p->enc[0] = 0;
    return p;
}
static int new_slot(void) { return nslots++; }

/* read r into xN, report via doorbell */
static void P_READ_R(const sr_t *r, const char *op, int reg)
{
    int s = new_slot();
    MOV64(reg, SENTINEL);
    add_probe(r->name, r, op, s);
    MRS(reg, r);
    STR(reg, 9, DB_SLOT0 + s * 8);
}
static void P_READ(const sr_t *r) { P_READ_R(r, "read", 0); }
static void P_WRITE_VAL(const sr_t *r, uint64_t v, const char *op)
{
    MOV64(1, v);
    add_probe(r->name, r, op, -1);
    MSR(r, 1);
    emit(ISB);
}
/* read (x0), write x0|bits (x1), read back (x2), write x0 back */
static void P_RW(const sr_t *r, uint64_t bits)
{
    P_READ_R(r, "read", 0);
    MOV64(1, bits);
    ORR(1, 1, 0);
    add_probe(r->name, r, bits ? "write(old|bits)" : "write(same)", -1);
    MSR(r, 1);
    emit(ISB);
    P_READ_R(r, "read-back", 2);
    add_probe(r->name, r, "write(restore)", -1);
    MSR(r, 0);
    emit(ISB);
}
static void P_INSN(const char *label, uint32_t word)
{
    add_probe(label, NULL, "exec", -1);
    emit(word);
}
static void P_INSN_RES(const char *label, uint32_t word, int reg)
{
    int s = new_slot();
    add_probe(label, NULL, "exec", s);
    emit(word);
    STR(reg, 9, DB_SLOT0 + s * 8);
}

/* ---------------------------------------------------------------- units
 * Pass 1 (dry): enumerate unit labels.  Pass 2 (child): emit only unit u. */
#define MAX_UNITS 200
static bool dry; static int unit_idx, unit_active, nunits;
static char unit_labels[MAX_UNITS][48];
static bool unit_begin(const char *label)
{
    int u = unit_idx++;
    if (dry) {
        if (u < MAX_UNITS) { snprintf(unit_labels[u], sizeof unit_labels[u], "%s", label); nunits = u + 1; }
        return false;
    }
    return u == unit_active;
}
static void U_READ(const sr_t *r) { if (unit_begin(r->name)) P_READ(r); }
static void U_RW(const sr_t *r, uint64_t bits) { if (unit_begin(r->name)) P_RW(r, bits); }
static void U_INSN(const char *label, uint32_t w) { if (unit_begin(label)) P_INSN(label, w); }

/* ------------------------------------------------------------- vm/vcpu */
static hv_vcpu_t vcpu; static hv_vcpu_exit_t *vexit;
static _Atomic uint64_t deadline_ns;

static void *watchdog(void *arg)
{
    (void)arg;
    for (;;) {
        usleep(20000);
        uint64_t d = atomic_load(&deadline_ns);
        if (d && now_ns() > d) hv_vcpus_exit(&vcpu, 1);
    }
    return NULL;
}
static void set_reg(hv_reg_t r, uint64_t v) { CHECK(hv_vcpu_set_reg(vcpu, r, v)); }
static uint64_t get_reg(hv_reg_t r) { uint64_t v = 0; CHECK(hv_vcpu_get_reg(vcpu, r, &v)); return v; }
static hv_return_t set_sys(hv_sys_reg_t r, uint64_t v)
{
    hv_return_t rc = hv_vcpu_set_sys_reg(vcpu, r, v);
    if (rc != HV_SUCCESS && verbose) fprintf(stderr, "  set_sys(0x%x)=0x%" PRIx64 " -> %s\n", r, v, hv_err(rc));
    return rc;
}
static uint64_t get_sys(hv_sys_reg_t r) { uint64_t v = 0; hv_vcpu_get_sys_reg(vcpu, r, &v); return v; }

static void gen_vectors(uint32_t tbl_off, const sr_t *esr, const sr_t *elr, unsigned db_esr, unsigned db_elr)
{
    uint64_t handler = GUEST_BASE + tbl_off + 0x800;
    for (int i = 0; i < 16; i++) { asm_begin(tbl_off + i * 0x80); emit(enc_B((int64_t)handler - (int64_t)here())); }
    asm_begin(tbl_off + 0x800);
    MRS(10, esr); MRS(11, elr);
    STR(10, 9, db_esr); STR(11, 9, db_elr);
    emit(enc_UBFX(12, 10, 26, 6));                       /* x12 = EC                        */
    emit(enc_CMPI(12, 0x15)); emit(enc_BCOND(0, 24));    /* SVC: ELR is already past insn   */
    emit(enc_CMPI(12, 0x16)); emit(enc_BCOND(0, 16));    /* HVC                             */
    emit(enc_CMPI(12, 0x17)); emit(enc_BCOND(0, 8));     /* SMC                             */
    ADDI(11, 11, 4);                                     /* everything else: skip the insn  */
    MSR(elr, 11);
    emit(ERET);
}
static void install_fixed_code(void)
{
    gen_vectors(VBAR1_OFF, &R_ESR_EL1, &R_ELR_EL1, DB_EXC1_ESR, DB_EXC1_ELR);
    gen_vectors(VBAR2_OFF, &R_ESR_EL2, &R_ELR_EL2, DB_EXC2_ESR, DB_EXC2_ELR);
    /* GXF entry stub: report, gexit; if gexit falls through, end the test */
    asm_begin(GXF_OFF);
    MOV64(12, 0x67656E7465720000ULL);
    STR(12, 9, DB_GXF_HIT);
    emit(GEXIT);
    MOV64(12, 0xBAD);
    STR(12, 9, DB_DONE);
    /* stage 1: identity map 0..4GiB with 1GiB blocks, AttrIndx 0 = Tagged Normal WB */
    uint64_t *pt = (uint64_t *)(gmem + PT_OFF);
    for (uint64_t i = 0; i < 4; i++) pt[i] = (i << 30) | 0x701ULL;
    /* guest stage 2: identity 0..4GiB, block, MemAttr=Normal WB, S2AP=RW, SH=inner, AF */
    uint64_t *s2 = (uint64_t *)(gmem + S2PT_OFF);
    for (uint64_t i = 0; i < 4; i++) s2[i] = (i << 30) | 0x7FDULL;
    sys_icache_invalidate(gmem, TESTS_OFF);
    next_code_off = TESTS_OFF;
}

static bool vm_open(bool el2)
{
    hv_vm_config_t cfg = hv_vm_config_create();
    if (el2) {
        hv_return_t r = hv_vm_config_set_el2_enabled(cfg, true);
        if (r != HV_SUCCESS) { printf("hv_vm_config_set_el2_enabled: %s\n", hv_err(r)); return false; }
    }
    hv_return_t r = hv_vm_create(cfg);
    if (r != HV_SUCCESS) { printf("hv_vm_create(el2=%d): 0x%x %s\n", el2, (unsigned)r, hv_err(r)); return false; }
    gmem = mmap(NULL, GUEST_SIZE, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
    if (gmem == MAP_FAILED) { perror("mmap"); exit(1); }
    CHECK(hv_vm_map(gmem, GUEST_BASE, GUEST_SIZE, HV_MEMORY_READ | HV_MEMORY_WRITE | HV_MEMORY_EXEC));
    CHECK(hv_vcpu_create(&vcpu, &vexit, NULL));
    install_fixed_code();
    return true;
}
static void vm_close(void)
{
    hv_vcpu_destroy(vcpu);
    hv_vm_unmap(GUEST_BASE, GUEST_SIZE);
    hv_vm_destroy();
    munmap(gmem, GUEST_SIZE);
}

typedef struct { const char *name; bool el2vm; int el; bool tidcp; bool s2; } vmode_t;

static void prepare(const vmode_t *m, bool mmu, bool mte)
{
    for (int i = 0; i < 31; i++) set_reg((hv_reg_t)(HV_REG_X0 + i), 0);
    set_reg(HV_REG_X9, DOORBELL);
    set_reg(HV_REG_PC, GUEST_BASE + code_off);
    set_sys(HV_SYS_REG_SP_EL0, STACK_TOP);
    set_sys(HV_SYS_REG_SP_EL1, STACK_TOP);
    if (mte && try_force_mte) {
        uint64_t pfr1 = get_sys(HV_SYS_REG_ID_AA64PFR1_EL1);
        hv_return_t rc = hv_vcpu_set_sys_reg(vcpu, HV_SYS_REG_ID_AA64PFR1_EL1, (pfr1 & ~(0xFULL << 8)) | (2ULL << 8));
        printf("  [--try-force-mte] hv_vcpu_set_sys_reg(ID_AA64PFR1_EL1.MTE=2): %s, now 0x%" PRIx64 "\n",
               hv_err(rc), get_sys(HV_SYS_REG_ID_AA64PFR1_EL1));
    }
    uint64_t sctlr = SCTLR_EL1_RES1 | (mmu ? SCTLR_M_C_I : 0) | (mte ? SCTLR_ATA : 0);
    hv_return_t rc = set_sys(HV_SYS_REG_SCTLR_EL1, sctlr);
    if (mte) {
        uint64_t got = get_sys(HV_SYS_REG_SCTLR_EL1);
        printf("  host set SCTLR_EL1=0x%" PRIx64 " (M|C|I|ATA|ATA0): rc=%s, read back 0x%" PRIx64 " -> ATA bits %s\n",
               sctlr, hv_err(rc), got, (got & SCTLR_ATA) == SCTLR_ATA ? "retained" : "NOT retained");
        if (rc != HV_SUCCESS) set_sys(HV_SYS_REG_SCTLR_EL1, sctlr & ~SCTLR_ATA);
    }
    set_sys(HV_SYS_REG_VBAR_EL1, GUEST_BASE + VBAR1_OFF);
    set_sys(HV_SYS_REG_ESR_EL1, 0);
    set_sys(HV_SYS_REG_ELR_EL1, 0);
    set_sys(HV_SYS_REG_TCR_EL1, mmu ? TCR_EL1_VAL : 0);
    set_sys(HV_SYS_REG_TTBR0_EL1, mmu ? GUEST_BASE + PT_OFF : 0);
    set_sys(HV_SYS_REG_MAIR_EL1, mmu ? MAIR_EL1_VAL : 0);
    if (m->el2vm) {
        set_sys(HV_SYS_REG_SP_EL2, STACK_TOP);
        set_sys(HV_SYS_REG_SCTLR_EL2, SCTLR_EL2_RES1);
        set_sys(HV_SYS_REG_VBAR_EL2, GUEST_BASE + VBAR2_OFF);
        set_sys(HV_SYS_REG_HCR_EL2, HCR_RW | HCR_ATA | (m->tidcp ? HCR_TIDCP : 0) | (m->s2 ? HCR_VM : 0));
        set_sys(HV_SYS_REG_VTCR_EL2, m->s2 ? VTCR_EL2_VAL : 0);
        set_sys(HV_SYS_REG_VTTBR_EL2, m->s2 ? GUEST_BASE + S2PT_OFF : 0);
        set_sys(HV_SYS_REG_CPTR_EL2, CPTR_EL2_RES1);
        set_sys(HV_SYS_REG_MDCR_EL2, 0);
        set_sys(HV_SYS_REG_ESR_EL2, 0);
        set_sys(HV_SYS_REG_ELR_EL2, 0);
    }
    unsigned cpsr = m->el == 2 ? 0x3c9 : 0x3c5;
    hv_return_t crc = hv_vcpu_set_reg(vcpu, HV_REG_CPSR, cpsr);
    if (crc != HV_SUCCESS || verbose) printf("  set CPSR=0x%x: %s\n", cpsr, hv_err(crc));
}

/* run until DB_DONE, a fatal condition, or a timeout.  returns 0 on DONE */
static int run_guest(void)
{
    uint64_t pend1 = 0, pend2 = 0;
    for (int iter = 0; iter < 600; iter++) {
        atomic_store(&deadline_ns, now_ns() + 2000000000ULL);
        hv_return_t r = hv_vcpu_run(vcpu);
        atomic_store(&deadline_ns, 0);
        if (r != HV_SUCCESS) { push_ev(EV_FATAL, 0, 0, 0, hv_err(r)); return -1; }
        if (vexit->reason == HV_EXIT_REASON_CANCELED) { push_ev(EV_TIMEOUT, get_reg(HV_REG_PC), 0, 0, "watchdog"); return -1; }
        if (vexit->reason == HV_EXIT_REASON_VTIMER_ACTIVATED) { hv_vcpu_set_vtimer_mask(vcpu, true); continue; }
        if (vexit->reason != HV_EXIT_REASON_EXCEPTION) { push_ev(EV_FATAL, 0, 0, 0, "unknown exit reason"); return -1; }

        uint64_t syn = vexit->exception.syndrome, far = vexit->exception.virtual_address;
        uint64_t ipa = vexit->exception.physical_address, pc = get_reg(HV_REG_PC);
        unsigned ec = fld(syn, 26, 6);
        if (verbose)
            fprintf(stderr, "    exit EC=0x%02x (%s) ESR=0x%" PRIx64 " PC=0x%" PRIx64 " FAR=0x%" PRIx64 " IPA=0x%" PRIx64 "\n",
                    ec, ec_name(ec), syn, pc, far, ipa);
        switch (ec) {
        case 0x24: case 0x25: {
            if (ipa >= DOORBELL && ipa < DOORBELL + DB_SIZE) {
                bool isv = fld(syn, 24, 1), wnr = fld(syn, 6, 1);
                unsigned srt = fld(syn, 16, 5);
                if (!isv || !wnr) { push_ev(EV_FATAL, pc, syn, far, "doorbell access without ISV/WnR"); return -1; }
                uint64_t val = srt == 31 ? 0 : get_reg((hv_reg_t)(HV_REG_X0 + srt));
                uint64_t off = ipa - DOORBELL;
                switch (off) {
                case DB_EXC1_ESR: pend1 = val; break;
                case DB_EXC1_ELR: push_ev(EV_EXC1, val, pend1, 0, NULL); break;
                case DB_EXC2_ESR: pend2 = val; break;
                case DB_EXC2_ELR: push_ev(EV_EXC2, val, pend2, 0, NULL); break;
                case DB_GXF_HIT:  push_ev(EV_GXF, pc, 0, 0, NULL); break;
                case DB_DONE:     return 0;
                default:
                    if (off >= DB_SLOT0 && (off - DB_SLOT0) / 8 < 128) { unsigned s = (unsigned)((off - DB_SLOT0) / 8); slot_val[s] = val; slot_set[s] = true; }
                }
                set_reg(HV_REG_PC, pc + 4);
                break;
            }
            char b[96]; snprintf(b, sizeof b, "unexpected data abort IPA=0x%" PRIx64 " FAR=0x%" PRIx64, ipa, far);
            push_ev(EV_FATAL, pc, syn, far, b); return -1;
        }
        case 0x18:
            push_ev(EV_VMMTRAP, pc, syn, 0, NULL);
            if (syn & 1) { unsigned rt = fld(syn, 5, 5); if (rt != 31) set_reg((hv_reg_t)(HV_REG_X0 + rt), 0); }
            set_reg(HV_REG_PC, pc + 4);
            break;
        case 0x16: push_ev(EV_HVC, pc, syn, 0, NULL); break;   /* PC already past the HVC */
        case 0x17: push_ev(EV_SMC, pc, syn, 0, NULL); set_reg(HV_REG_PC, pc + 4); break;
        case 0x20: case 0x21: {
            char b[96]; snprintf(b, sizeof b, "instruction abort FAR=0x%" PRIx64 " IPA=0x%" PRIx64, far, ipa);
            push_ev(EV_FATAL, pc, syn, far, b); return -1;
        }
        default:
            push_ev(EV_OTHER, pc, syn, far, NULL);
            set_reg(HV_REG_PC, pc + 4);
            break;
        }
    }
    push_ev(EV_TIMEOUT, get_reg(HV_REG_PC), 0, 0, "exit-count cap"); return -1;
}

/* ---------------------------------------------------------- reporting */
static const ev_t *find_ev(const probe_t *p)
{
    for (int i = 0; i < nevents; i++) {
        const ev_t *e = &events[i];
        if (e->type == EV_GXF || e->type == EV_FATAL || e->type == EV_TIMEOUT) continue;
        if (e->pc == p->addr) return e;
        if (e->type == EV_HVC && e->pc == p->addr + 4) return e;
        if ((e->type == EV_EXC1 || e->type == EV_EXC2) && e->pc == p->addr + 4) {
            unsigned ec = fld(e->esr, 26, 6);
            if (ec == 0x15 || ec == 0x16 || ec == 0x17) return e;
        }
    }
    return NULL;
}
static void fmt_sysreg_trap(uint64_t syn, char *b, size_t n)
{
    unsigned op0 = fld(syn, 20, 2), op2 = fld(syn, 17, 3), op1 = fld(syn, 14, 3), crn = fld(syn, 10, 4);
    unsigned rt = fld(syn, 5, 5), crm = fld(syn, 1, 4), dir = fld(syn, 0, 1);
    snprintf(b, n, "S%u_%u_C%u_C%u_%u %s x%u", op0, op1, crn, crm, op2, dir ? "read ->" : "write <-", rt);
}
static void print_probe(const probe_t *p, bool aborted_before)
{
    char out[200] = "";
    const ev_t *e = find_ev(p);
    if (e) {
        char d[96] = "";
        unsigned ec = fld(e->esr, 26, 6);
        if (ec == 0x18) fmt_sysreg_trap(e->esr, d, sizeof d);
        switch (e->type) {
        case EV_VMMTRAP: snprintf(out, sizeof out, "TRAP->VMM      ESR=0x%08" PRIx64 " EC=0x%02x %s%s%s", e->esr, ec, ec_name(ec), d[0] ? " " : "", d); break;
        case EV_EXC1:    snprintf(out, sizeof out, "GUEST-EXC@EL1  ESR=0x%08" PRIx64 " EC=0x%02x %s%s%s", e->esr, ec, ec_name(ec), d[0] ? " " : "", d); break;
        case EV_EXC2:    snprintf(out, sizeof out, "GUEST-EXC@EL2  ESR=0x%08" PRIx64 " EC=0x%02x %s%s%s", e->esr, ec, ec_name(ec), d[0] ? " " : "", d); break;
        case EV_HVC:     snprintf(out, sizeof out, "EXIT->VMM      ESR=0x%08" PRIx64 " EC=0x16 HVC64", e->esr); break;
        case EV_SMC:     snprintf(out, sizeof out, "EXIT->VMM      ESR=0x%08" PRIx64 " EC=0x17 SMC64", e->esr); break;
        default:         snprintf(out, sizeof out, "EXIT->VMM      ESR=0x%08" PRIx64 " EC=0x%02x %s", e->esr, ec, ec_name(ec)); break;
        }
    } else if (aborted_before) {
        snprintf(out, sizeof out, "NO RESULT      (test aborted before this point)");
    } else if (p->slot >= 0) {
        if (slot_set[p->slot]) snprintf(out, sizeof out, "NATIVE         value=0x%016" PRIx64, slot_val[p->slot]);
        else snprintf(out, sizeof out, "NO RESULT      (no doorbell store seen)");
    } else {
        snprintf(out, sizeof out, "NATIVE         (no trap, no guest exception)");
    }
    printf("  %-22s %-16s %-16s %s\n", p->label, p->enc, p->op, out);
}

static void begin_test(void)
{
    nprobes = 0; nevents = 0; nslots = 0;
    memset(slot_set, 0, sizeof slot_set);
    code_off = next_code_off; next_code_off += TEST_STRIDE;
    if (code_off + TEST_STRIDE > GUEST_SIZE) { fprintf(stderr, "out of guest code space\n"); exit(1); }
    asm_begin(code_off);
}
static void end_test(void)
{
    MOV64(3, 0);
    STR(3, 9, DB_DONE);
    sys_icache_invalidate(gmem + code_off, TEST_STRIDE);
    int rc = run_guest();
    uint64_t abort_pc = UINT64_MAX;
    const ev_t *bad = NULL;
    for (int i = 0; i < nevents; i++)
        if (events[i].type == EV_FATAL || events[i].type == EV_TIMEOUT) { bad = &events[i]; abort_pc = events[i].pc; break; }
    for (int i = 0; i < nprobes; i++) {
        bool aborted_before = rc != 0 && probes[i].addr > abort_pc && !find_ev(&probes[i]);
        print_probe(&probes[i], aborted_before);
    }
    for (int i = 0; i < nevents; i++)
        if (events[i].type == EV_GXF) printf("  [event] GXF entry stub at 0x%" PRIx64 " was reached (genter jumped there)\n", GUEST_BASE + GXF_OFF);
    if (bad)
        printf("  [%s] pc=0x%" PRIx64 " ESR=0x%" PRIx64 " %s\n", bad->type == EV_TIMEOUT ? "TIMEOUT" : "ABORT", bad->pc, bad->esr, bad->msg);
    if (verbose)
        for (int i = 0; i < nevents; i++)
            fprintf(stderr, "    ev type=%d pc=0x%" PRIx64 " esr=0x%" PRIx64 " %s\n", events[i].type, events[i].pc, events[i].esr, events[i].msg);
}

/* --------------------------------------------------------------- tests */
static void test_arch(const vmode_t *m)
{
    U_READ(&R_CURRENTEL); U_READ(&R_MIDR); U_READ(&R_PFR0); U_READ(&R_PFR1);
    U_READ(&R_MMFR1); U_READ(&R_ISAR1); U_READ(&R_SCTLR_EL1);
    if (m->el == 2) { U_READ(&R_HCR_EL2); U_READ(&R_VTTBR_EL2); U_READ(&R_SCTLR_EL2); }
    U_INSN("UDF (.long 0)", 0);
    U_INSN("SVC #0", SVC0);
    U_INSN("HVC #0", HVC0);
    U_INSN("SMC #0", SMC0);
    U_INSN("BRK #0", BRK0);
    U_INSN("WFI", WFI);
}

static void test_mte(const vmode_t *m)
{
    (void)m;
    if (!unit_begin("MTE sequence")) return;
    P_READ(&R_PFR1);
    P_READ(&R_SCTLR_EL1);
    P_WRITE_VAL(&R_GCR_EL1, 0xFFFD, "write 0xFFFD");   /* exclude every tag but 1 */
    P_READ_R(&R_GCR_EL1, "read-back", 2);
    MOV64(1, GUEST_BASE + DATA_OFF);                     /* x1 = untagged address */
    P_INSN_RES("IRG x0, x1, xzr", enc_IRG(0, 1, 31), 0);   /* expect tag 1 in bits 59:56 */
    P_INSN_RES("ADDG x2, x1, #16, #3", enc_ADDG(2, 1, 1, 3), 2); /* expect tag 3 */
    P_INSN("STG x2, [x2]", enc_STG(2, 2));
    MOVR(3, 1);
    ADDI(3, 3, 16);
    P_INSN_RES("LDG x3, [x3]", enc_LDG(3, 3), 3);         /* expect tag 3 if tag storage works */
}

static void test_apple(const vmode_t *m)
{
    U_RW(&A_SPRR_CONFIG_EL1, 1);
    U_RW(&A_SPRR_PPERM_EL1, 0);
    U_READ(&A_SPRR_UPERM_EL0); U_READ(&A_SPRR_AMRANGE_EL1); U_READ(&A_SPRR_PMPRR_EL1); U_READ(&A_SPRR_UMPRR_EL1);
    U_READ(&A_GXF_CONFIG_EL1); U_READ(&A_GXF_ENTRY_EL1); U_READ(&A_GXF_PABENTRY_EL1); U_READ(&A_CURRENTG);
    U_READ(&A_ASPSR_GL1); U_READ(&A_SP_GL1); U_READ(&A_ESR_GL1); U_READ(&A_ELR_GL1); U_READ(&A_VBAR_GL1);
    U_RW(&A_APCTL_EL1, 0); U_READ(&A_APSTS_EL1); U_RW(&A_KERNKEYLO_EL1, 0);
    U_READ(&A_AMXIDR_EL1); U_READ(&A_AMX_CONFIG_EL1); U_READ(&A_AMX_STATE_T_EL1);
    U_RW(&A_VMSA_LOCK_EL1, 0);
    U_RW(&A_CTRR_A_LWR_EL1, 0); U_READ(&A_CTRR_A_UPR_EL1); U_READ(&A_CTRR_A_CTL_EL1);
    U_READ(&A_HID0); U_READ(&A_HID4); U_READ(&A_HID11); U_READ(&A_EHID0);
    U_RW(&A_PMCR0_EL1, 0); U_READ(&A_PMC0);
    U_READ(&A_CYC_OVRD); U_READ(&A_ACC_CFG); U_READ(&A_IPI_SR); U_READ(&A_IMP_MSR_RO_CTRL0);
    U_READ(&A_ACNTPCT_EL0); U_READ(&A_ACNTVCT_EL0);
    if (m->el == 2) {
        U_RW(&A_SPRR_CONFIG_EL2, 1); U_READ(&A_SPRR_PPERM_EL2); U_READ(&A_SPRR_AMRANGE_EL2);
        U_READ(&A_GXF_CONFIG_EL2); U_READ(&A_GXF_ENTRY_EL2); U_READ(&A_GXF_PABENTRY_EL2);
        U_READ(&A_SP_GL2); U_READ(&A_ESR_GL2); U_READ(&A_ELR_GL2);
        U_RW(&A_TAG_OFFSET_EL2, 0); U_RW(&A_APCTL_EL2, 0); U_READ(&A_APSTS_EL2);
        U_RW(&A_VMSA_LOCK_EL2, 0); U_READ(&A_AMX_CONFIG_EL2);
        U_READ(&A_CTRR_A_LWR_EL2); U_READ(&A_CTRR_A_CTL_EL2);
        U_READ(&A_AHCR_EL2); U_READ(&A_MMU_SFAR_EL2); U_READ(&A_HPFAR_GL2);
    }
}

static void test_gxf(const vmode_t *m)
{
    const sr_t *cfg = m->el == 2 ? &A_GXF_CONFIG_EL2 : &A_GXF_CONFIG_EL1;
    const sr_t *ent = m->el == 2 ? &A_GXF_ENTRY_EL2 : &A_GXF_ENTRY_EL1;
    if (!unit_begin("GXF sequence")) return;
    P_READ_R(cfg, "read", 5);
    MOV64(1, 1); ORR(1, 1, 5);
    add_probe(cfg->name, cfg, "write(old|1)", -1); MSR(cfg, 1); emit(ISB);
    P_READ_R(cfg, "read-back", 2);
    P_WRITE_VAL(ent, GUEST_BASE + GXF_OFF, "write stub addr");
    P_READ_R(ent, "read-back", 2);
    P_INSN("genter", GENTER);
    emit(ISB);
    P_READ(&A_CURRENTG);
    P_INSN("gexit (not in GL)", GEXIT);
    add_probe(cfg->name, cfg, "write(restore)", -1); MSR(cfg, 5); emit(ISB);
}

static void test_tidcp(const vmode_t *m)
{
    (void)m;
    U_READ(&A_HID0); U_READ(&A_SPRR_CONFIG_EL1); U_READ(&A_CTRR_A_LWR_EL1);
    U_READ(&A_ACNTPCT_EL0); U_READ(&A_APCTL_EL1); U_READ(&A_PMC0);
    U_READ(&A_GXF_CONFIG_EL1);
}

/* -------------------------------------------------------------- driver */
typedef void (*testfn_t)(const vmode_t *);

static void run_test(const vmode_t *m, testfn_t fn, const char *title, bool mmu, bool mte)
{
    printf("\n--- %s ---\n", title);
    dry = true; unit_idx = 0; nunits = 0;
    fn(m);
    dry = false;
    for (int u = 0; u < nunits; u++) {
        fflush(stdout); fflush(stderr);
        pid_t pid = fork();
        if (pid < 0) { perror("fork"); exit(1); }
        if (pid == 0) {
            pthread_t wd; pthread_create(&wd, NULL, watchdog, NULL);
            if (!vm_open(m->el2vm)) _exit(3);
            begin_test();
            prepare(m, mmu, mte);
            unit_idx = 0; unit_active = u;
            fn(m);
            end_test();
            fflush(stdout);
            _exit(0);
        }
        int st = 0;
        waitpid(pid, &st, 0);
        if (WIFSIGNALED(st))
            printf("  %-22s %-16s %-16s CRASHED        Hypervisor.framework killed the process: signal %d (%s), an internal report_fixme/unimplemented path\n",
                   unit_labels[u], "", "", WTERMSIG(st), strsignal(WTERMSIG(st)));
        else if (WEXITSTATUS(st) != 0)
            printf("  %-22s %-16s %-16s CHILD FAILED   exit status %d\n", unit_labels[u], "", "", WEXITSTATUS(st));
    }
}

static void run_all(const vmode_t *m)
{
    printf("\n=================== MODE: %s ===================\n", m->name);
    run_test(m, test_arch, "architectural sanity + trapping instructions", false, false);
    if (m->el == 1)
        run_test(m, test_mte, "MTE: ID field, SCTLR.ATA, GCR, IRG/ADDG (MMU on, Tagged Normal memory), STG/LDG round trip", true, true);
    run_test(m, test_apple, "Apple IMPLEMENTATION DEFINED sysregs (the darwin-vm fork emulates all of these)", false, false);
    run_test(m, test_gxf, "GXF: enable GXF_CONFIG, set GXF_ENTRY to a stub, execute genter / gexit", false, false);
}

/* ----------------------------------------------------------- host info */
static void print_feature_regs(void)
{
    hv_vcpu_config_t c = hv_vcpu_config_create();
    struct { const char *n; hv_feature_reg_t r; } regs[] = {
        { "ID_AA64PFR0_EL1",  HV_FEATURE_REG_ID_AA64PFR0_EL1 },
        { "ID_AA64PFR1_EL1",  HV_FEATURE_REG_ID_AA64PFR1_EL1 },
        { "ID_AA64PFR2_EL1",  HV_FEATURE_REG_ID_AA64PFR2_EL1 },
        { "ID_AA64DFR0_EL1",  HV_FEATURE_REG_ID_AA64DFR0_EL1 },
        { "ID_AA64ISAR0_EL1", HV_FEATURE_REG_ID_AA64ISAR0_EL1 },
        { "ID_AA64ISAR1_EL1", HV_FEATURE_REG_ID_AA64ISAR1_EL1 },
        { "ID_AA64ISAR2_EL1", HV_FEATURE_REG_ID_AA64ISAR2_EL1 },
        { "ID_AA64MMFR0_EL1", HV_FEATURE_REG_ID_AA64MMFR0_EL1 },
        { "ID_AA64MMFR1_EL1", HV_FEATURE_REG_ID_AA64MMFR1_EL1 },
        { "ID_AA64MMFR2_EL1", HV_FEATURE_REG_ID_AA64MMFR2_EL1 },
        { "ID_AA64MMFR3_EL1", HV_FEATURE_REG_ID_AA64MMFR3_EL1 },
        { "ID_AA64SMFR0_EL1", HV_FEATURE_REG_ID_AA64SMFR0_EL1 },
    };
    uint64_t pfr0 = 0, pfr1 = 0, mmfr1 = 0, isar1 = 0, isar2 = 0, dfr0 = 0;
    printf("Guest-visible feature registers (hv_vcpu_config_get_feature_reg):\n");
    for (size_t i = 0; i < sizeof regs / sizeof regs[0]; i++) {
        uint64_t v = 0;
        hv_return_t rc = hv_vcpu_config_get_feature_reg(c, regs[i].r, &v);
        printf("  %-18s 0x%016" PRIx64 "%s\n", regs[i].n, v, rc == HV_SUCCESS ? "" : hv_err(rc));
        if (regs[i].r == HV_FEATURE_REG_ID_AA64PFR0_EL1) pfr0 = v;
        if (regs[i].r == HV_FEATURE_REG_ID_AA64PFR1_EL1) pfr1 = v;
        if (regs[i].r == HV_FEATURE_REG_ID_AA64MMFR1_EL1) mmfr1 = v;
        if (regs[i].r == HV_FEATURE_REG_ID_AA64ISAR1_EL1) isar1 = v;
        if (regs[i].r == HV_FEATURE_REG_ID_AA64ISAR2_EL1) isar2 = v;
        if (regs[i].r == HV_FEATURE_REG_ID_AA64DFR0_EL1) dfr0 = v;
    }
    printf("  decoded: PFR0.EL2=%u EL3=%u SVE=%u SEL2=%u | PFR1.BT=%u MTE=%u MTE_frac=%u MTEX=%u SME=%u | MMFR1.VH=%u PAN=%u HAFDBS=%u\n",
           fld(pfr0, 8, 4), fld(pfr0, 12, 4), fld(pfr0, 32, 4), fld(pfr0, 36, 4),
           fld(pfr1, 0, 4), fld(pfr1, 8, 4), fld(pfr1, 40, 4), fld(pfr1, 52, 4), fld(pfr1, 24, 4),
           fld(mmfr1, 8, 4), fld(mmfr1, 20, 4), fld(mmfr1, 0, 4));
    printf("  decoded: ISAR1.APA=%u API=%u GPA=%u GPI=%u | ISAR2.APA3=%u GPA3=%u | DFR0.PMUVer=%u DebugVer=%u\n",
           fld(isar1, 4, 4), fld(isar1, 8, 4), fld(isar1, 24, 4), fld(isar1, 28, 4),
           fld(isar2, 12, 4), fld(isar2, 8, 4), fld(dfr0, 8, 4), fld(dfr0, 0, 4));
    os_release(c);
}

static bool host_info(void)
{
    bool el2 = false;
    hv_return_t rc = hv_vm_config_get_el2_supported(&el2);
    printf("hv_vm_config_get_el2_supported: %s -> %s\n", hv_err(rc), el2 ? "YES (EL2 / nested virtualization available)" : "NO");
    uint32_t ipa_max = 0, ipa_def = 0;
    hv_vm_config_get_max_ipa_size(&ipa_max); hv_vm_config_get_default_ipa_size(&ipa_def);
    hv_ipa_granule_t gran = 0; hv_vm_config_get_default_ipa_granule(&gran);
    printf("IPA size: default %u bits, max %u bits; default IPA granule enum=%d\n", ipa_def, ipa_max, (int)gran);
    size_t svl = 0; hv_sme_config_get_max_svl_bytes(&svl);
    printf("SME max SVL: %zu bytes\n", svl);
    print_feature_regs();
    return el2;
}

int main(int argc, char **argv)
{
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--verbose")) verbose = true;
        else if (!strcmp(argv[i], "--no-el2")) no_el2 = true;
        else if (!strcmp(argv[i], "--try-force-mte")) try_force_mte = true;
        else if (!strcmp(argv[i], "--selftest")) { selftest(); return 0; }
        else if (!strncmp(argv[i], "--modes=", 8)) modes = argv[i] + 8;
        else { fprintf(stderr, "usage: %s [--verbose] [--no-el2] [--try-force-mte] [--modes=12345] [--selftest]\n", argv[0]); return 2; }
    }
    setvbuf(stdout, NULL, _IOLBF, 0);

    printf("hvf_probe: standalone Hypervisor.framework probe\n");
    bool el2 = host_info();

    vmode_t m1 = { "EL1 in a plain VM (no EL2)", false, 1, false, false };
    if (strchr(modes, '1')) run_all(&m1);

    if (el2 && !no_el2) {
        vmode_t m2 = { "EL2 in an EL2-enabled (nested-virt) VM", true, 2, false, false };
        vmode_t m3 = { "EL1 under guest EL2 (nested-virt VM, HCR_EL2.VM=0, TIDCP=0)", true, 1, false, false };
        vmode_t m4 = { "EL1 under guest EL2 (nested-virt VM, HCR_EL2.VM=1 identity stage-2, TIDCP=0)", true, 1, false, true };
        vmode_t m5 = { "EL1 under guest EL2 (nested-virt VM, HCR_EL2.VM=1, TIDCP=1)", true, 1, true, true };
        if (strchr(modes, '2')) run_all(&m2);
        if (strchr(modes, '3')) run_all(&m3);
        if (strchr(modes, '4')) run_all(&m4);
        if (strchr(modes, '5')) {
            printf("\n=================== MODE: %s ===================\n", m5.name);
            run_test(&m5, test_tidcp, "do IMP-DEF accesses from EL1 trap to the *guest* EL2 (EC=0x18 in the EL2 handler)?", false, false);
        }
    } else if (!el2) {
        printf("\nEL2 not supported by Hypervisor.framework on this host: skipping nested-virt modes.\n");
    }
    printf("\ndone.\n");
    return 0;
}
