/* macOS-only diagnostic: compare observe vs user-initiated host vCPU QoS.
 * No guest modifications or instruction callbacks. QEMU plugins/core.c queues
 * the init hook onto each vCPU; pthread/qos.h documents the self-only setter.
 * Build with cc -shared -undefined dynamic_lookup -O2 -fPIC
 * $(pkg-config --cflags glib-2.0)
 * -I qemu-sptm/include/plugins tools/re/host_qos_probe.c -o OUTPUT.dylib
 * Load alongside the unchanged milestone observer: -plugin OUTPUT.dylib,
 * out=NEW_PATH,mode=observe|user-initiated
 * This requests QoS, not CPU affinity or guaranteed physical core placement.
 */
#include <pthread.h>
#include <pthread/qos.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <qemu-plugin.h>

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;
static FILE *output;
static int change_qos;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;

static void cpu_init(unsigned index, void *unused)
{
    qos_class_t before = QOS_CLASS_UNSPECIFIED, after = QOS_CLASS_UNSPECIFIED;
    int before_priority = 0, after_priority = 0;
    uint64_t tid = 0;
    int tid_rc = pthread_threadid_np(NULL, &tid);
    int before_rc = pthread_get_qos_class_np(pthread_self(), &before, &before_priority);
    int set_rc = change_qos ? pthread_set_qos_class_self_np(QOS_CLASS_USER_INITIATED, 0) : 0;
    int after_rc = pthread_get_qos_class_np(pthread_self(), &after, &after_priority);
    pthread_mutex_lock(&lock);
    fprintf(output, "cpu=%u tid=%llu tid_rc=%d before=%u before_priority=%d before_rc=%d changed=%d set_rc=%d after=%u after_priority=%d after_rc=%d\n",
            index, (unsigned long long)tid, tid_rc, before, before_priority,
            before_rc, change_qos, set_rc, after, after_priority, after_rc);
    fflush(output);
    pthread_mutex_unlock(&lock);
    if (tid_rc || before_rc || set_rc || after_rc ||
        (change_qos && after != QOS_CLASS_USER_INITIATED)) {
        abort(); /* A failed scheduling experiment must not look successful. */
    }
}

QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id,
        const qemu_info_t *info, int argc, char **argv)
{
    const char *path = NULL;
    int mode_seen = 0;
    for (int i = 0; i < argc; i++) {
        if (!strncmp(argv[i], "out=", 4)) {
            path = argv[i] + 4;
        } else if (!strcmp(argv[i], "mode=observe")) {
            mode_seen++;
            change_qos = 0;
        } else if (!strcmp(argv[i], "mode=user-initiated")) {
            mode_seen++;
            change_qos = 1;
        } else {
            return -1;
        }
    }
    if (!path || mode_seen != 1 || !(output = fopen(path, "wx"))) {
        return -1;
    }
    qemu_plugin_register_vcpu_init_cb(id, cpu_init, NULL);
    return 0;
}
