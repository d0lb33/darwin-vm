// Read-only exact-24A5430a TXM signature diagnosis through QEMU's plugin API.
// No guest instructions, registers, memory, permissions or results are changed.
// Link addresses and field offsets come from the pinned TXM's disassembly;
// this is diagnostic evidence, never a performance or uninstrumented pass.
#include <glib.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <qemu-plugin.h>

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;
typedef struct { struct qemu_plugin_register *r[31]; bool have[31]; GByteArray *bytes; } CPU;
static CPU cpus[64];
static GMutex lock;
static FILE *output;
static unsigned events;
static gint translation_witnesses;
static const uint64_t points[] = {
    0xfffffff017046860ULL, // compilation check entry: x0 signature object
    0xfffffff01704687cULL, // configuration +0x4b in w9
    0xfffffff0170468a4ULL, // environment predicate result in w0
    0xfffffff0170468c8ULL, // compilation hash match result in w0
    0xfffffff017047d90ULL, // complete compilation check result in w0
    0xfffffff0170467e0ULL, // local ad-hoc configuration in w9
    0xfffffff017046808ULL, // local ad-hoc predicate result in w0
    0xfffffff017047da4ULL, // complete local ad-hoc result in w0
    0xfffffff017047dc0ULL, // final ad-hoc rejection
    0xfffffff017047f30ULL, // final signature completion, only OOP-JIT class 2
};
static const uint32_t words[] = {0xd503237f,0x37000089,0x36000080,0x340000a0,
    0x72181c1f,0x37000089,0x36000080,0x72181c1f,0x52844040,0xa9417bfd};
static uint64_t reg(CPU *c, unsigned n) {
    uint64_t value = 0;
    // Handle zero is a valid register (x0); QEMU appends register data.
    g_byte_array_set_size(c->bytes,0);
    if (!c->have[n] || !qemu_plugin_read_register(c->r[n], c->bytes) || c->bytes->len != 8)
        return UINT64_MAX;
    memcpy(&value, c->bytes->data, 8);
    return GUINT64_FROM_LE(value);
}
static void memory(CPU *c, const char *name, uint64_t address, size_t length) {
    fprintf(output, ",\"%s_address\":\"0x%016" PRIx64 "\",\"%s\":", name,address,name);
    if (!address || !qemu_plugin_read_memory_vaddr(address,c->bytes,length)) {
        fputs("null",output); return;
    }
    fputc('"',output);
    for (size_t i=0;i<c->bytes->len;i++) fprintf(output,"%02x",c->bytes->data[i]);
    fputc('"',output);
}
static uint64_t pointer(CPU *c, uint64_t address) {
    uint64_t value=0;
    if (address && qemu_plugin_read_memory_vaddr(address,c->bytes,8))
        memcpy(&value,c->bytes->data,8);
    return GUINT64_FROM_LE(value);
}
static void execute(unsigned cpu, void *userdata) {
    if (cpu>=64) return;
    CPU *c=&cpus[cpu];uint64_t runtime_pc=(uint64_t)(uintptr_t)userdata;
    uint64_t pc=0;
    for(unsigned j=0;j<G_N_ELEMENTS(points);j++)
        if((runtime_pc&0xfffff)==(points[j]&0xfffff))pc=points[j];
    uint64_t slide=runtime_pc-pc;
    g_mutex_lock(&lock);
    uint64_t x0=reg(c,0), x1=reg(c,1), x8=reg(c,8), x9=reg(c,9), x20=reg(c,20);
    if (pc==0xfffffff017047f30ULL && x20!=2) { g_mutex_unlock(&lock);return; }
    if (events++>=512) { g_mutex_unlock(&lock);return; }
    fprintf(output,"{\"runtime_pc\":\"0x%016"PRIx64"\",\"event\":%u,\"cpu\":%u,\"pc\":\"0x%016"PRIx64"\","
            "\"x0\":\"0x%016"PRIx64"\",\"x1\":\"0x%016"PRIx64"\","
            "\"x8\":\"0x%016"PRIx64"\",\"x9\":\"0x%016"PRIx64"\","
            "\"x20\":\"0x%016"PRIx64"\"",runtime_pc,events,cpu,pc,x0,x1,x8,x9,x20);
    if (pc==points[0]) {
        uint64_t env=pointer(c,x0), config=pointer(c,env);
        memory(c,"signature_hash",x0+0x40,72);
        memory(c,"configuration",config,96);
        memory(c,"environment",env,0xd0);
        memory(c,"authorized_hash",0xfffffff017088ce0ULL+slide,72);
        memory(c,"device_state",0xfffffff017088db0ULL+slide,16);
    }
    fputs("}\n",output);fflush(output);g_mutex_unlock(&lock);
}
static void init_cpu(unsigned cpu, void *unused) {
    if (cpu>=64) return;
    CPU *c=&cpus[cpu];c->bytes=g_byte_array_new();
    GArray *regs=qemu_plugin_get_registers();
    for (unsigned i=0;i<regs->len;i++) {
        qemu_plugin_reg_descriptor *r=&g_array_index(regs,qemu_plugin_reg_descriptor,i);
        unsigned n; char tail;
        if (sscanf(r->name,"x%u%c",&n,&tail)==1 && n<31) { c->r[n]=r->handle;c->have[n]=true; }
    }
    g_array_free(regs,true);
    g_mutex_lock(&lock);
    fprintf(output,"{\"cpu_init\":%u,\"x0_available\":%s}\n",cpu,c->have[0]?"true":"false");
    fflush(output);g_mutex_unlock(&lock);
}
static void translate(struct qemu_plugin_tb *tb, void *unused) {
    if (g_atomic_int_get(&translation_witnesses)<8) {
        g_mutex_lock(&lock);
        if(g_atomic_int_add(&translation_witnesses,1)<8)
            fprintf(output,"{\"translated_tb\":\"0x%016"PRIx64"\"}\n",qemu_plugin_tb_vaddr(tb));
        fflush(output);g_mutex_unlock(&lock);
    }
    for (size_t i=0;i<qemu_plugin_tb_n_insns(tb);i++) {
        struct qemu_plugin_insn *insn=qemu_plugin_tb_get_insn(tb,i);
        uint64_t pc=qemu_plugin_insn_vaddr(insn);
        // TRACE_GUEST3 independently observed these actual runtime VAs.
        for (unsigned j=0;j<G_N_ELEMENTS(points);j++) if (pc==points[j]) {
            uint32_t word=0;
            if(qemu_plugin_insn_data(insn,&word,4)!=4 || GUINT32_FROM_LE(word)!=words[j])continue;
            qemu_plugin_register_vcpu_insn_exec_cb(insn,execute,QEMU_PLUGIN_CB_R_REGS,(void *)(uintptr_t)pc);
            break;
        }
    }
}
QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id, const qemu_info_t *info,int argc,char **argv) {
    if (argc!=1 || strncmp(argv[0],"output=",7) || strcmp(info->target_name,"aarch64")) return -1;
    output=fopen(argv[0]+7,"wx");if(!output)return -1;
    fputs("{\"scope\":\"read-only-TXM-signature-diagnosis\",\"event_limit\":512}\n",output);fflush(output);
    qemu_plugin_register_vcpu_init_cb(id,init_cpu,NULL);
    qemu_plugin_register_vcpu_tb_trans_cb(id,translate,NULL);
    return 0;
}
