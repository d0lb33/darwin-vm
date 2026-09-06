/* The experimental MMIO helper mirrors bounded result records into owned RAM.
 * The NVMe build and host worker retain their original stderr reporting. */
#ifdef DVM_DRIVER_MMIO
#include <stdio.h>
int DVMReport(FILE *, const char *, ...) __attribute__((format(printf,2,3)));
#define fprintf DVMReport
#endif
