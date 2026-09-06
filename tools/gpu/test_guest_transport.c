/* Host-only parser test: never invokes the supervisor or opens a console. */
#define main supervisor_main_not_called
#include "guest_transport.c"
#undef main
#include <assert.h>
int main(void) {
    int worker[2], ack[2]; assert(!pipe(worker) && !pipe(ack));
    uint64_t expected=0;
    char good[]="DVMGPU_IN 0 00ff3141";
    assert(incoming(good,&expected,worker[1],ack[1]) && expected==4);
    unsigned char bytes[4]; assert(read(worker[0],bytes,4)==4 && bytes[0]==0 && bytes[1]==255 && bytes[2]==49 && bytes[3]==65);
    char text[64]={0}; assert(read(ack[0],text,sizeof(text))==13 && !strcmp(text,"DVMGPU_ACK 4\n"));
    const char *bad[]={"DVMGPU_IN 0 00","DVMGPU_IN -1 00","DVMGPU_IN +4 00", "DVMGPU_IN 18446744073709551620 00", "DVMGPU_IN 4 0", "DVMGPU_IN 4 gg", "DVMGPU_IN 4 00 junk", "DVMGPU_IN 4 ", "DVMGPU_IN 4 00 ", "OTHER 4 00"};
    for(unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);i++) {
        char line[LINE];strcpy(line,bad[i]);
        assert(!incoming(line,&expected,worker[1],ack[1]) && expected==4);
    }
    expected=UINT64_MAX; char overflow[]="DVMGPU_IN 18446744073709551615 00";
    assert(!incoming(overflow,&expected,worker[1],ack[1]));
    close(worker[0]);close(worker[1]);close(ack[0]);close(ack[1]);
    puts("PASS valid binary payload, ACK, stale offset, signed/overflow offset, malformed hex, extra fields");
    return 0;
}
