#pragma once
/* Host -> guest and guest -> host session status inside the existing owned
 * shared RAM. This is DVM's own development ABI, not Apple register semantics.
 *
 * The region 0x210..0x1000 is unused by every existing consumer: the transport
 * headers live at 0x40/0x80 (darwin_gpu_transport.h), the exact AIR digest at
 * 0x100, the audit head/consumed words at 0x180/0x188, the present
 * configuration at 0x200 for 16 bytes (present_layout.h DVM_PRESENT_CONFIG),
 * and the audit slots begin at 0x1000. Nothing here changes the device model.
 *
 * Every field is one naturally aligned 64-bit word, so a reader never observes
 * a torn value and no lock is needed between the poller and its writer. The
 * host publishes DVM_SESSION_MAGIC once, before the first guest process maps
 * the region; the guest refuses to act on any other value.
 */
#include <stdint.h>

#define DVM_SESSION_BASE                0x220u
#define DVM_SESSION_MAGIC_OFFSET        (DVM_SESSION_BASE + 0x00u) /* host  */
#define DVM_SESSION_RETIRE_GENERATION   (DVM_SESSION_BASE + 0x08u) /* host  */
#define DVM_SESSION_GUEST_GENERATION    (DVM_SESSION_BASE + 0x10u) /* guest */
#define DVM_SESSION_GUEST_PID           (DVM_SESSION_BASE + 0x18u) /* guest */
#define DVM_SESSION_GUEST_REVISION      (DVM_SESSION_BASE + 0x20u) /* guest */
#define DVM_SESSION_GUEST_STATE         (DVM_SESSION_BASE + 0x28u) /* guest */
#define DVM_SESSION_GUEST_REQUESTS      (DVM_SESSION_BASE + 0x30u) /* guest */
#define DVM_SESSION_GUEST_IMPORTS       (DVM_SESSION_BASE + 0x38u) /* guest */
#define DVM_SESSION_GUEST_IMPORTS_DONE  (DVM_SESSION_BASE + 0x40u) /* guest */
#define DVM_SESSION_END                 (DVM_SESSION_BASE + 0x48u)

/* "DVMSESS1" little-endian. */
#define DVM_SESSION_MAGIC UINT64_C(0x31535345534d5644)

enum {
    DVM_SESSION_STATE_ABSENT = 0,
    DVM_SESSION_STATE_STARTING = 1,
    DVM_SESSION_STATE_RUNNING = 2,
    DVM_SESSION_STATE_RETIRING = 3,
    DVM_SESSION_STATE_RETIRED = 4,
};

/* backboardd's own writable staging root. The launchd job's disposable Data
 * child owns it and the executable carries the matching absolute-path
 * read-write sandbox exception. */
#define DVM_SESSION_STAGE_ROOT "/private/var/tmp/dvm-gpu-runner"

/* The boot-trusted driver compiled into the bootstrap. A staged image reports
 * its own number, so a generation always carries exactly one revision. */
#define DVM_SESSION_BUILTIN_REVISION UINT64_C(1)
