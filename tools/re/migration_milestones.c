/* Read-only TCG milestone observer for the captured iOS 24A5430a checkpoint.
 * No guest breakpoints, writes, or calls. Instrument only explicitly listed
 * instruction PCs; register callbacks are absent on all other instructions.
 * Build: cc -shared -undefined dynamic_lookup -O2 -fPIC $(pkg-config --cflags
 * glib-2.0) -I qemu-sptm/include/plugins tools/re/migration_milestones.c
 * -o /tmp/dvm/migration_milestones.dylib
 * Load: -plugin /tmp/dvm/migration_milestones.dylib,out=PATH,slide=0x16294000
 * Add scans-only=on for one x0 read per scan return, with no guest-memory reads.
 * systemapp-only=on observes the verified APP_SYSAPP_SIGNATURE1 image PCs.
 * Compare against a plugin-free run before treating observed timing as a
 * benchmark. Scan/validate entries are attempts, not completed installations.
 */
#include <glib.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <qemu-plugin.h>

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;
static FILE *output;
static GMutex output_lock;
static uint64_t slide;
static bool scans_only;
static bool systemapp_only;
static bool snapshots_only;
typedef struct {
    struct qemu_plugin_register *regs[5];
    unsigned available;
    GByteArray *data;
} CPU;
static CPU cpus[64];
typedef struct { uint64_t pc; const char *name; bool absolute; } Site;
static Site sites[] = {
    {0x25e8ef02c, "DM_LOG"},
    {0x1aaedb598, "LS_SCAN_ENTRY"},
    {0x1aae92d48, "BUNDLE_VALIDATE_ENTRY"},
    /* One epilogue, native Boolean result in x0 before retab. */
    {0x1aaedb9d8, "LS_SCAN_RETURN"},
    /* APP_SYSAPP_SIGNATURE1: image base 0x10442c000, native call/return
     * bytes verified by inspect_systemapp_signature.py. These results
     * are signature checks, not completed app installs. */
    {0x104432c98, "SYSAPP_SIGNATURE_CALL", true},
    {0x104432c9c, "SYSAPP_SIGNATURE_RETURN", true},
    {0x2ac03cab8, "SNAPSHOT_ENCODE_ENTRY"},
    {0x2ac03cc5c, "SNAPSHOT_ENCODE_RETURN"},
    {0x2ac03cd3c, "SNAPSHOT_FINALIZE_CALL"},
    {0x2ac03cd40, "SNAPSHOT_FINALIZE_RETURN"},
    {0x102c1e034, "MIGRATOR_CAPTURE_WAIT_RETURN", true},
};

static uint64_t read64(const uint8_t *p)
{
    uint64_t value;
    memcpy(&value, p, 8);
    return GUINT64_FROM_LE(value);
}

static char *read_string(CPU *cpu, uint64_t address)
{
    if (!address || !qemu_plugin_read_memory_vaddr(address, cpu->data, 32)) {
        return g_strdup("");
    }
    uint8_t head[32];
    memcpy(head, cpu->data->data, 32);
    uint64_t ptr = 0;
    size_t size = 0;
    bool utf16 = false;
    unsigned flags = head[8] | (head[9] << 8);
    if ((read64(head) & 0x7fffffffff8ULL) == 0x1e6f2d408 + slide) {
        uint32_t word;
        memcpy(&word, head + 8, 4);
        size = (GUINT32_FROM_LE(word) >> 20) * 2;
        ptr = address + 12;
        utf16 = true;
    } else if (flags == 0x7c8) {
        ptr = read64(head + 16);
        size = read64(head + 24);
    } else if (flags == 0x78c) {
        ptr = address + 17;
        size = head[16];
    }
    if (!ptr || !size || size > 4096 ||
        !qemu_plugin_read_memory_vaddr(ptr, cpu->data, size)) {
        return g_strdup("");
    }
    if (utf16) {
        char *str = g_utf16_to_utf8((gunichar2 *)cpu->data->data, size / 2,
                                   NULL, NULL, NULL);
        return str ? str : g_strdup("");
    }
    return g_utf8_make_valid((char *)cpu->data->data, size);
}

static void event(unsigned index, void *userdata)
{
    if (index >= G_N_ELEMENTS(cpus)) {
        return;
    }
    CPU *cpu = &cpus[index];
    Site *site = userdata;
    uint64_t regs[4] = {0};
    for (unsigned i = 0; i < ((scans_only || systemapp_only) ? 1 : 4); i++) {
        g_byte_array_set_size(cpu->data, 0);
        if ((cpu->available & (1u << i)) && qemu_plugin_read_register(cpu->regs[i], cpu->data)
            && cpu->data->len >= 8) {
            regs[i] = read64(cpu->data->data);
        }
    }
    char *value;
    if (site == &sites[0]) {
        value = read_string(cpu, regs[2]);
        g_byte_array_set_size(cpu->data, 0);
        if ((cpu->available & (1u << 4)) && qemu_plugin_read_register(cpu->regs[4], cpu->data)
            && cpu->data->len >= 8) {
            uint64_t sp = read64(cpu->data->data);
            if (qemu_plugin_read_memory_vaddr(sp, cpu->data, 32)) {
                uint8_t args[32];
                memcpy(args, cpu->data->data, 32);
                for (unsigned i = 0; i < 4; i++) {
                    char *arg = read_string(cpu, read64(args + 8 * i));
                    char *next = g_strdup_printf("%s | %s", value, arg);
                    g_free(value);
                    g_free(arg);
                    value = next;
                }
            }
        }
    } else if (site == &sites[1] || site == &sites[2]) {
        uint64_t bundle = regs[site == &sites[1] ? 2 : 0];
        uint64_t name = 0;
        if (bundle && qemu_plugin_read_memory_vaddr(bundle + 48, cpu->data, 8)) {
            name = read64(cpu->data->data);
        }
        value = read_string(cpu, name);
    } else {
        value = g_strdup("");
    }
    /* TSV with escaped text, one atomic record per sparse milestone. */
    char *escaped = g_strescape(value, NULL);
    g_mutex_lock(&output_lock);
    fprintf(output, "%.6f\t%u\t%s\t0x%" PRIx64 "\t0x%" PRIx64
            "\t0x%" PRIx64 "\t0x%" PRIx64 "\t%s\n",
            g_get_monotonic_time() / 1e6, index, site->name,
            regs[0], regs[1], regs[2], regs[3], escaped);
    fflush(output);
    g_mutex_unlock(&output_lock);
    g_free(escaped);
    g_free(value);
}

static void cpu_init(unsigned index, void *userdata)
{
    if (index >= G_N_ELEMENTS(cpus)) {
        return;
    }
    cpus[index].data = g_byte_array_new();
    GArray *regs = qemu_plugin_get_registers();
    for (unsigned j = 0; j < regs->len; j++) {
        qemu_plugin_reg_descriptor *reg = &g_array_index(regs, qemu_plugin_reg_descriptor, j);
        for (unsigned i = 0; i < 5; i++) {
            char name[8];
            if (i == 4) {
                strcpy(name, "sp");
            } else {
                snprintf(name, sizeof(name), "x%u", i);
            }
            if (!strcmp(reg->name, name)) {
                cpus[index].regs[i] = reg->handle;
                cpus[index].available |= 1u << i;
            }
        }
    }
    g_array_free(regs, true);
}

static void translate(struct qemu_plugin_tb *tb, void *userdata)
{
    for (size_t i = 0; i < qemu_plugin_tb_n_insns(tb); i++) {
        struct qemu_plugin_insn *insn = qemu_plugin_tb_get_insn(tb, i);
        uint64_t pc = qemu_plugin_insn_vaddr(insn);
        for (unsigned s = snapshots_only ? 6 : (systemapp_only ? 4 : (scans_only ? 3 : 0));
             s < (snapshots_only ? G_N_ELEMENTS(sites) : (systemapp_only ? 6 : 4)); s++) {
            if (pc == sites[s].pc + (sites[s].absolute ? 0 : slide)) {
                qemu_plugin_register_vcpu_insn_exec_cb(insn, event,
                    QEMU_PLUGIN_CB_R_REGS, &sites[s]);
            }
        }
    }
}

QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id,
        const qemu_info_t *info, int argc, char **argv)
{
    const char *path = NULL;
    for (int i = 0; i < argc; i++) {
        if (g_str_has_prefix(argv[i], "out=")) {
            path = argv[i] + 4;
        } else if (!strcmp(argv[i], "snapshots-only=on")) {
            snapshots_only = true;
        } else if (!strcmp(argv[i], "systemapp-only=on")) {
            systemapp_only = true;
        } else if (!strcmp(argv[i], "scans-only=on")) {
            scans_only = true;
        } else if (g_str_has_prefix(argv[i], "slide=")) {
            slide = strtoull(argv[i] + 6, NULL, 0);
        } else {
            return -1;
        }
    }
    if (!path || !slide || !(output = fopen(path, "wx"))) {
        return -1;
    }
    qemu_plugin_register_vcpu_init_cb(id, cpu_init, NULL);
    qemu_plugin_register_vcpu_tb_trans_cb(id, translate, NULL);
    return 0;
}
