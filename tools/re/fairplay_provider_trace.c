/* Read-only, sparse register capture of the 24A5430a FairPlay provider.
 * Native selector21 at fffffff009b526ac dispatches through provider9b52504
 * to9c65e88. The provider's six indirect calls and matching return sites are
 * from APP_FRESH1/passbook-re/fairplay-provider.txt. Capture call targets and
 * results before attempting to interpret its obfuscated error computation.
 * No guest writes, breakpoints, or forced statuses. Exclude diagnostic runs
 * from performance comparisons. Fixed kernel slide0x20000000 for this image.
 * Build: cc -shared -undefined dynamic_lookup -O2 -fPIC
 * $(pkg-config --cflags glib-2.0) -I qemu-sptm/include/plugins
 * tools/re/fairplay_provider_trace.c -o OUTPUT.dylib
 * Load: -plugin OUTPUT.dylib,out=NEW_PATH[,blocks=on]
 * blocks=on follows only the first FairPlay provider thread, at most 20,000
 * blocks, through its exact fileset text range. Native AES startup sites are
 * independent sparse witnesses of driver progress.
 */
#include <glib.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdatomic.h>
#include <string.h>
#include <qemu-plugin.h>

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;
static FILE *output;
static FILE *blocks;
static GMutex lock;
static atomic_int block_phase;
static atomic_uint_fast64_t block_owner;
static atomic_uint block_count;
static atomic_uint_fast64_t aes_owner;
static const uint64_t sites[] = {
    0xfffffff029c65e88,
    0xfffffff029c66040, 0xfffffff029c66044,
    0xfffffff029c660dc, 0xfffffff029c660e0,
    0xfffffff029c66138, 0xfffffff029c6613c,
    0xfffffff029c66238, 0xfffffff029c6623c,
    0xfffffff029c662f8, 0xfffffff029c662fc,
    0xfffffff029c66340, 0xfffffff029c66344,
    0xfffffff029c66368,
    /* APP_FAIRPLAY_PROVIDER2 resolves the fourth call to9d3db20. Follow its
     * first call, two computed dispatch branches and final status store. */
    0xfffffff029d3db20,
    0xfffffff029d3dccc, 0xfffffff029d3dcd0,
    0xfffffff029d3df8c, 0xfffffff029d3e178,
    0xfffffff029d3e184,
    /* Native AES start / DMA setup / superclass registration (aes-driver.txt).
     * Sparse startup witnesses; never change return values. */
    0xfffffff0293ee0c0, 0xfffffff0293ee640,
    0xfffffff0293ee67c, 0xfffffff0293ee6b8,
    0xfffffff0293ee6e8, 0xfffffff0293ee724,
    0xfffffff0293ee760, 0xfffffff0293ee8b8,
    0xfffffff0293ee9f8, 0xfffffff0293ee9fc,
    0xfffffff0293eea00,
    /* IOAESAccelerator::start, resolved by APP_AES_START1. */
    0xfffffff029f15e44, 0xfffffff029f16120,
    0xfffffff029f16130, 0xfffffff029f161bc,
    0xfffffff029f16228, 0xfffffff029f16250,
    0xfffffff029f162b0, 0xfffffff029f162c0,
    0xfffffff029f1633c, 0xfffffff029f1637c,
    0xfffffff029f16410,
    0xfffffff029f161b8,
    0xfffffff02b1f3c70, 0xfffffff02b1f3e4c,
    0xfffffff0285cda08,
    0xfffffff029f165c0, 0xfffffff029f165d8,
    0xfffffff029f17064, 0xfffffff029f1722c,
    0xfffffff029f17230, 0xfffffff029f18a6c,
    0xfffffff029f18adc, 0xfffffff029f18cd4,
    0xfffffff029f18dc4, 0xfffffff029f18ec0,
    0xfffffff029f18ec4, 0xfffffff029f18fb0,
    0xfffffff029f16bd0, 0xfffffff029f16cac,
    0xfffffff0293eee74, 0xfffffff0293eef1c,
    0xfffffff0293eef20, 0xfffffff0293ef104,
    0xfffffff0293ef180,
};
static struct {
    struct qemu_plugin_register *regs[32];
    GByteArray *bytes;
    uint32_t available;
    struct qemu_plugin_register *thread_reg;
    bool has_thread_reg;
} cpus[64];

static uint64_t thread_id(unsigned cpu)
{
    uint64_t value;
    g_byte_array_set_size(cpus[cpu].bytes, 0);
    if (!cpus[cpu].has_thread_reg ||
        !qemu_plugin_read_register(cpus[cpu].thread_reg, cpus[cpu].bytes) ||
        cpus[cpu].bytes->len != 8) {
        fprintf(stderr, "FairPlay trace cannot read TPIDR_EL1\n");
        _Exit(3);
    }
    memcpy(&value, cpus[cpu].bytes->data, 8);
    return GUINT64_FROM_LE(value);
}

static void record(FILE *dest, unsigned cpu, uint64_t pc)
{
    if (cpu >= G_N_ELEMENTS(cpus)) {
        return;
    }
    uint64_t values[32];
    for (unsigned i = 0; i < 32; i++) {
        g_byte_array_set_size(cpus[cpu].bytes, 0);
        /* The opaque handle for writable register0 is legitimately NULL:
         * plugins/api.c encodes the register index in the handle itself. */
        if (!(cpus[cpu].available & (UINT32_C(1) << i)) ||
            !qemu_plugin_read_register(cpus[cpu].regs[i], cpus[cpu].bytes) ||
            cpus[cpu].bytes->len != 8) {
            fprintf(stderr, "FairPlay trace missing register %u on cpu %u\n", i, cpu);
            _Exit(3);
        }
        memcpy(&values[i], cpus[cpu].bytes->data, 8);
        values[i] = GUINT64_FROM_LE(values[i]);
    }
    g_mutex_lock(&lock);
    fprintf(dest, "%.6f\t%u\t0x%" PRIx64,
            g_get_monotonic_time() / 1e6, cpu, pc);
    for (unsigned i = 0; i < 32; i++) {
        fprintf(dest, "\t0x%" PRIx64, values[i]);
    }
    fputc('\n', dest);
    fflush(dest);
    g_mutex_unlock(&lock);
}

static void event(unsigned cpu, void *opaque)
{
    uint64_t pc = *(const uint64_t *)opaque;
    if (pc == 0xfffffff029f161b8) {
        atomic_store(&aes_owner, thread_id(cpu));
    }
    if ((pc == 0xfffffff02b1f3c70 || pc == 0xfffffff02b1f3e4c ||
         pc == 0xfffffff0285cda08) &&
        (!atomic_load(&aes_owner) || thread_id(cpu) != atomic_load(&aes_owner))) {
        return;
    }
    if (pc == 0xfffffff029f161bc) {
        atomic_store(&aes_owner, 0);
    }
    if (blocks && pc == sites[0]) {
        int expected = 0;
        if (atomic_compare_exchange_strong(&block_phase, &expected, -1)) {
            uint64_t owner = thread_id(cpu);
            atomic_store(&block_owner, owner);
            fprintf(blocks, "# owner-thread=0x%" PRIx64 " limit=20000\n", owner);
            fflush(blocks);
            atomic_store(&block_phase, 1);
        }
    }
    record(output, cpu, pc);
    if (blocks && pc == 0xfffffff029c66368 &&
        atomic_load(&block_phase) == 1 &&
        thread_id(cpu) == atomic_load(&block_owner)) {
        atomic_store(&block_phase, 2);
        fprintf(stderr, "fairplay-trace: first provider complete, blocks=%u\n",
                atomic_load(&block_count));
    }
}

static void block(unsigned cpu, void *opaque)
{
    if (atomic_load(&block_phase) != 1 ||
        thread_id(cpu) != atomic_load(&block_owner)) {
        return;
    }
    if (atomic_fetch_add(&block_count, 1) >= 20000) {
        atomic_store(&block_phase, 3);
        fprintf(stderr, "fairplay-trace: truncated at20000 blocks\n");
        return;
    }
    record(blocks, cpu, (uint64_t)(uintptr_t)opaque);
}

static void init(unsigned cpu, void *unused)
{
    if (cpu >= G_N_ELEMENTS(cpus)) {
        fprintf(stderr, "FairPlay trace supports at most64 CPUs\n");
        _Exit(3);
    }
    cpus[cpu].bytes = g_byte_array_new();
    GArray *regs = qemu_plugin_get_registers();
    for (unsigned j = 0; j < regs->len; j++) {
        qemu_plugin_reg_descriptor *r = &g_array_index(regs, qemu_plugin_reg_descriptor, j);
        if (!g_ascii_strcasecmp(r->name, "TPIDR_EL1")) {
            cpus[cpu].thread_reg = r->handle;
            cpus[cpu].has_thread_reg = true;
        }
    }
    for (unsigned i = 0; i < 32; i++) {
        char name[8];
        if (i == 31) {
            strcpy(name, "sp");
        } else {
            snprintf(name, sizeof(name), "x%u", i);
        }
        for (unsigned j = 0; j < regs->len; j++) {
            qemu_plugin_reg_descriptor *r = &g_array_index(regs, qemu_plugin_reg_descriptor, j);
            if (!strcmp(r->name, name)) {
                cpus[cpu].regs[i] = r->handle;
                cpus[cpu].available |= UINT32_C(1) << i;
            }
        }
    }
    g_array_free(regs, true);
}

static void translate(struct qemu_plugin_tb *tb, void *unused)
{
    uint64_t pc = qemu_plugin_tb_vaddr(tb);
    /* Exact FairPlayIOKit __TEXT_EXEC.__text extent in APP_FRESH1/bootkc,
     * plus the measured kernel slide. Trace only the first provider thread. */
    if (blocks && pc >= 0xfffffff029b50950 && pc < 0xfffffff029d894b0) {
        qemu_plugin_register_vcpu_tb_exec_cb(tb, block,
                QEMU_PLUGIN_CB_R_REGS, (void *)(uintptr_t)pc);
    }
    for (size_t i = 0; i < qemu_plugin_tb_n_insns(tb); i++) {
        struct qemu_plugin_insn *insn = qemu_plugin_tb_get_insn(tb, i);
        uint64_t pc = qemu_plugin_insn_vaddr(insn);
        for (unsigned s = 0; s < G_N_ELEMENTS(sites); s++) {
            if (pc == sites[s]) {
                qemu_plugin_register_vcpu_insn_exec_cb(insn, event,
                    QEMU_PLUGIN_CB_R_REGS, (void *)&sites[s]);
            }
        }
    }
}

QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id,
        const qemu_info_t *info, int argc, char **argv)
{
    if ((argc != 1 && argc != 2) || !g_str_has_prefix(argv[0], "out=") ||
        (argc == 2 && strcmp(argv[1], "blocks=on")) ||
        !(output = fopen(argv[0] + 4, "wx"))) {
        return -1;
    }
    if (argc == 2) {
        char *path = g_strconcat(argv[0] + 4, ".blocks.tsv", NULL);
        blocks = fopen(path, "wx");
        g_free(path);
        if (!blocks) {
            return -1;
        }
    }
    qemu_plugin_register_vcpu_init_cb(id, init, NULL);
    qemu_plugin_register_vcpu_tb_trans_cb(id, translate, NULL);
    return 0;
}
