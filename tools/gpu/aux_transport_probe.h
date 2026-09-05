/* Disposable auxiliary-media experiment. Byte ownership: host writes header,
 * seed and replies; guest writes result and requests. No filesystem is mounted.
 * This copied transport is not a coherent shared GPU-memory interface. */
#include <stdint.h>
#include <stdlib.h>
#include <time.h>
#include <sys/disk.h>
#include <sys/ioctl.h>

#ifdef DVM_AUX_HEADER_DIAG
#include <pthread.h>
#include <stdatomic.h>
static atomic_uint aux_diag_stage;
static void aux_diag_mark(unsigned stage) {
    atomic_store(&aux_diag_stage,stage);
    fprintf(stderr,"GPU_LOAD_AUX_STAGE stage=%u pid=%d\n",stage,getpid());
}
static void *aux_diag_heartbeat(void *unused) {
    (void)unused;
    for(;;) {
        struct timespec nap={5,0};nanosleep(&nap,NULL);
        fprintf(stderr,"GPU_LOAD_AUX_ALIVE stage=%u pid=%d\n",atomic_load(&aux_diag_stage),getpid());
    }
    return NULL;
}
static void aux_diag_exit(void) {
    fprintf(stderr,"GPU_LOAD_AUX_EXIT stage=%u pid=%d\n",atomic_load(&aux_diag_stage),getpid());
}
#endif

#define AUX_PAGE 4096
#define AUX_BYTES (64u * 1024u * 1024u)
#define AUX_BULK (1024u * 1024u)
static const char aux_magic[] = "DVM-AUX-TRANSPORT-v1";
#ifndef DVM_AUX_UC_TRANSFER
static unsigned aux_attempts;
#define aux_read pread
#define aux_write pwrite
#else
static __typeof__(&IOConnectCallScalarMethod) aux_scalar;
static ssize_t aux_io(int client, void *buffer, size_t bytes, off_t offset, unsigned selector) {
    if(!bytes||bytes%AUX_PAGE||offset<0||offset%AUX_PAGE||
       bytes>AUX_BYTES||(uint64_t)offset>AUX_BYTES-bytes) {errno=EINVAL;return -1;}
    uint64_t inputs[]={(uintptr_t)buffer,bytes,(uint64_t)offset};
    kern_return_t kr=aux_scalar((io_connect_t)client,selector,inputs,3,NULL,NULL);
    if(kr) {
        fprintf(stderr,"GPU_LOAD_AUX_IO_ERROR selector=%u bytes=%zu offset=%lld kr=0x%x\n",selector,bytes,(long long)offset,kr);
        errno=EIO;return -1;
    }
    return bytes;
}
static ssize_t aux_read(int client, void *buffer, size_t bytes, off_t offset) {
    /* Exact guest: selector 0 uses kIODirectionIn=1 and NVMe read opcode 2. */
    return aux_io(client,buffer,bytes,offset,0);
}
static ssize_t aux_write(int client, const void *buffer, size_t bytes, off_t offset) {
    /* Selector 1 uses kIODirectionOut=2; a1390a4 sets NVMe write opcode 1. */
    return aux_io(client,(void *)buffer,bytes,offset,1);
}
#endif

static uint32_t aux_crc(const unsigned char *data, size_t length) {
    uint32_t crc=~0u;
    for(size_t i=0;i<length;i++) {
        crc^=data[i];
        for(int k=0;k<8;k++)crc=(crc>>1)^((0u-(crc&1u))&0xedb88320u);
    }
    return ~crc;
}
static double aux_now(void) {
    struct timespec ts;clock_gettime(CLOCK_MONOTONIC,&ts);
    return ts.tv_sec+ts.tv_nsec/1e9;
}
/* Optional fixed workload, configured in the host-owned first page. Timings
 * stay in guest CLOCK_MONOTONIC; no host/guest clock subtraction is valid.
 * Defer UART output until the batch ends so per-request logging cannot delay
 * the next request. The old timeout/recovery workload remains unchanged. */
static int aux_latency(int fd, const unsigned char *header,
                       unsigned char *packet, unsigned char *reply) {
    struct sample { double total,write,reads,max_read,sleep,max_sleep,verify; unsigned polls,crc_retries; int valid; } rows[64]={0};
    unsigned completed=0;int result=1;
    fprintf(stderr,"GPU_LOAD_AUX_LAT_BEGIN count=64 bytes=4096 guest_sleep_ns=1000000\n");
    for(uint32_t seq=1;seq<=64;seq++) {
        struct sample *r=&rows[seq-1];
        memset(packet,0,AUX_PAGE);memcpy(packet,header,64);memcpy(packet+64,&seq,4);
        for(unsigned i=72;i<AUX_PAGE;i++)packet[i]=(unsigned char)(i*13u+seq*17u);
        uint32_t crc=aux_crc(packet+72,AUX_PAGE-72);memcpy(packet+68,&crc,4);
        double start=aux_now(), t=start;
        if(aux_write(fd,packet,AUX_PAGE,0x10000)!=AUX_PAGE)goto done;
        r->write=aux_now()-t;
        while(aux_now()-start<5.0) {
            t=aux_now();
            if(aux_read(fd,reply,AUX_PAGE,0x20000)!=AUX_PAGE)goto done;
            double elapsed=aux_now()-t;r->reads+=elapsed;
            if(elapsed>r->max_read)r->max_read=elapsed;
            r->polls++;
            t=aux_now();
            if(!memcmp(reply,packet,68)) {
                memcpy(&crc,reply+68,4);
                if(crc==aux_crc(reply+72,AUX_PAGE-72)) {
                    r->valid=1;
                    for(unsigned i=72;i<AUX_PAGE;i++)if(reply[i]!=(unsigned char)(packet[i]^0xa5))r->valid=0;
                    r->verify+=aux_now()-t;
                    if(!r->valid){errno=EILSEQ;goto sample_done;}
                    break;
                }
                /* A read can race reply publication. Reject this snapshot
                 * but permit bounded polling to obtain the complete reply. */
                r->crc_retries++;
            }
            r->verify+=aux_now()-t;
            t=aux_now();struct timespec nap={0,1000000};nanosleep(&nap,NULL);
            elapsed=aux_now()-t;r->sleep+=elapsed;
            if(elapsed>r->max_sleep)r->max_sleep=elapsed;
        }
sample_done:
        r->total=aux_now()-start;completed=seq;
        if(r->total>=5.0){r->valid=0;errno=ETIMEDOUT;goto done;}
        if(!r->valid){if(errno!=EILSEQ)errno=ETIMEDOUT;goto done;}
    }
    result=0;
done:
    for(unsigned i=0;i<completed;i++) {
        struct sample *r=&rows[i];
        fprintf(stderr,"GPU_LOAD_AUX_LAT seq=%u valid=%d polls=%u crc_retries=%u total_ms=%.6f write_ms=%.6f read_ms=%.6f max_read_ms=%.6f sleep_ms=%.6f max_sleep_ms=%.6f verify_ms=%.6f\n",
            i+1,r->valid,r->polls,r->crc_retries,r->total*1000,r->write*1000,r->reads*1000,r->max_read*1000,r->sleep*1000,r->max_sleep*1000,r->verify*1000);
    }
    return result;
}
static int __attribute__((unused)) aux_transfer(int fd, const unsigned char *header) {
    int latency=!memcmp(header+128,"DVMLAT01",8);
    uint32_t count=0,sleep_ns=0;
    memcpy(&count,header+136,4);memcpy(&sleep_ns,header+140,4);
    if(latency&&(count!=64||sleep_ns!=1000000)){errno=EINVAL;return 1;}
    unsigned char *data=NULL,*packet=NULL,*reply=NULL;
    if(posix_memalign((void **)&data,16384,AUX_BULK)||
       posix_memalign((void **)&packet,16384,AUX_PAGE)||
       posix_memalign((void **)&reply,16384,AUX_PAGE)) {
        free(data);free(packet);free(reply);errno=ENOMEM;return 1;
    }
    int result=1;double start=aux_now();
    if(latency&&!memcmp(header+144,"DVMWAIT1",8)) {
        unsigned polls=0;int released=0;
        uint32_t wait_seconds=0;memcpy(&wait_seconds,header+152,4);
        if(!wait_seconds)wait_seconds=330; /* Original fixed-budget header. */
        if(wait_seconds!=330&&wait_seconds!=480){errno=EINVAL;goto done;}
        fprintf(stderr,"GPU_LOAD_AUX_WAIT version=1 budget_seconds=%u poll_ms=100\n",wait_seconds);
        while(aux_now()-start<wait_seconds) {
            if(aux_read(fd,reply,AUX_PAGE,0x30000)!=AUX_PAGE)goto done;
            polls++;
            uint32_t gate_crc=0;memcpy(&gate_crc,reply+72,4);
            if(!memcmp(reply,header,64)&&!memcmp(reply+64,"DVMGO001",8)&&gate_crc==aux_crc(reply,72)) {
                released=1;break;
            }
            struct timespec nap={0,100000000};nanosleep(&nap,NULL);
        }
        fprintf(stderr,"GPU_LOAD_AUX_RELEASE valid=%d polls=%u wait_seconds=%.6f\n",released,polls,aux_now()-start);
        if(!released){errno=ETIMEDOUT;goto done;}
        start=aux_now();
    }
    if(aux_read(fd,data,AUX_BULK,0x100000)!=AUX_BULK)goto done;
    double io_seconds=aux_now()-start;
    for(unsigned i=0;i<AUX_BULK;i++)if(data[i]!=(unsigned char)(i*37u+(i>>8)*11u+19u)){errno=EILSEQ;goto done;}
    fprintf(stderr,"GPU_LOAD_AUX_READ bytes=%u verified=1 seconds=%.6f\n",AUX_BULK,io_seconds);
    for(unsigned i=0;i<AUX_BULK;i++)data[i]^=0x5a;
    start=aux_now();
    if(aux_write(fd,data,AUX_BULK,0x400000)!=AUX_BULK)goto done;
    io_seconds=aux_now()-start;
    fprintf(stderr,"GPU_LOAD_AUX_WRITE bytes=%u seconds=%.6f crc=%08x\n",AUX_BULK,io_seconds,aux_crc(data,AUX_BULK));
    if(latency){result=aux_latency(fd,header,packet,reply);goto done;}
    for(uint32_t seq=1;seq<=10;seq++) {
        memset(packet,0,AUX_PAGE);memcpy(packet,header,64);
        memcpy(packet+64,&seq,4);
        for(unsigned i=72;i<AUX_PAGE;i++)packet[i]=(unsigned char)(i*13u+seq*17u);
        uint32_t crc=aux_crc(packet+72,AUX_PAGE-72);memcpy(packet+68,&crc,4);
        start=aux_now();
        if(aux_write(fd,packet,AUX_PAGE,0x10000)!=AUX_PAGE)goto done;
        unsigned polls=0;int valid=0;
        /* Sequence 9 is deliberately unanswered. Ten verifies recovery after
         * the deadline, without changing the input service or console mode. */
        double budget=seq==9?1.0:15.0;
        while(aux_now()-start<budget) {
            if(aux_read(fd,reply,AUX_PAGE,0x20000)!=AUX_PAGE)goto done;
            polls++;
            if(!memcmp(reply,packet,68)) {
                memcpy(&crc,reply+68,4);
                if(crc==aux_crc(reply+72,AUX_PAGE-72)) {
                    valid=1;
                    for(unsigned i=72;i<AUX_PAGE;i++)if(reply[i]!=(unsigned char)(packet[i]^0xa5))valid=0;
                    if(valid)break;
                }
            }
            struct timespec nap={0,1000000};nanosleep(&nap,NULL);
        }
        fprintf(stderr,"GPU_LOAD_AUX_PING seq=%u valid=%d polls=%u seconds=%.6f expected_timeout=%d\n",seq,valid,polls,aux_now()-start,seq==9);
        if(valid!=(seq!=9)){errno=valid?EPROTO:ETIMEDOUT;goto done;}
    }
    result=0;
done:
    fprintf(stderr,"GPU_LOAD_AUX_RESULT pass=%d errno=%d\n",!result,result?errno:0);
    free(data);free(packet);free(reply);return result;
}

#ifdef DVM_AUX_UC_TRANSFER
static int aux_probe_client(io_connect_t client) {
    uint64_t block=0,count=0;uint32_t outputs=1;
    kern_return_t kr=aux_scalar(client,2,NULL,0,&block,&outputs);
    fprintf(stderr,"GPU_LOAD_AUX_METADATA selector=2 kr=0x%x count=%u value=%llu\n",kr,outputs,(unsigned long long)block);
    if(kr||outputs!=1||block!=AUX_PAGE)return 1;
    outputs=1;kr=aux_scalar(client,3,NULL,0,&count,&outputs);
    fprintf(stderr,"GPU_LOAD_AUX_METADATA selector=3 kr=0x%x count=%u value=%llu\n",kr,outputs,(unsigned long long)count);
    if(kr||outputs!=1||count!=AUX_BYTES/AUX_PAGE)return 1;
    unsigned char *header=NULL;
#ifdef DVM_AUX_HEADER_DIAG
    aux_diag_mark(1); /* Capacity verified; next call allocates the buffer. */
#endif
    if(posix_memalign((void **)&header,16384,AUX_PAGE))return 1;
#ifdef DVM_AUX_HEADER_DIAG
    aux_diag_mark(2); /* Allocation returned; next operation touches the page. */
#endif
    memset(header,0xcc,AUX_PAGE);
#ifdef DVM_AUX_HEADER_DIAG
    aux_diag_mark(3); /* Page initialized; next call is selector 0. */
#endif
    ssize_t got=aux_read(client,header,AUX_PAGE,0);
#ifdef DVM_AUX_HEADER_DIAG
    aux_diag_mark(4); /* Synchronous read returned, including failure returns. */
#endif
    fprintf(stderr,"GPU_LOAD_AUX_HEADER bytes=%zd prefix=",got);
    for(unsigned i=0;i<32;i++)fprintf(stderr,"%02x",header[i]);
    fprintf(stderr,"\n");
    int ok=got==AUX_PAGE&&
        !memcmp(header,aux_magic,sizeof(aux_magic));
    fprintf(stderr,"GPU_LOAD_AUX_GUARD pass=%d bytes=%u\n",ok,AUX_BYTES);
#ifdef DVM_AUX_HEADER_DIAG
    fprintf(stderr,"GPU_LOAD_AUX_HEADER_ONLY pass=%d bytes=%zd crc=%08x\n",ok,got,aux_crc(header,AUX_PAGE));
    int result=!ok;
#else
    int result=ok?aux_transfer(client,header):1;
#endif
    free(header);return result;
}
#else
static int aux_probe_media(const char *bsd) {
    if(strncmp(bsd,"disk",4)||!bsd[4])return 1;
    for(const char *p=bsd+4;*p;p++)if(*p<'0'||*p>'9')return 1;
    char path[80];snprintf(path,sizeof(path),"/dev/r%s",bsd);
    int fd=open(path,O_RDONLY|O_NOCTTY);
    fprintf(stderr,"GPU_LOAD_AUX_OPEN path=%s fd=%d errno=%d\n",path,fd,fd<0?errno:0);
    if(fd<0)return 1;
    unsigned char *header=NULL;
    if(posix_memalign((void **)&header,16384,AUX_PAGE)){close(fd);return 1;}
    uint64_t count=0;uint32_t block=0;
    int ok=ioctl(fd,DKIOCGETBLOCKCOUNT,&count)==0&&ioctl(fd,DKIOCGETBLOCKSIZE,&block)==0&&
        count>0&&block==4096&&count<=AUX_BYTES/block&&count*block==AUX_BYTES&&
        pread(fd,header,AUX_PAGE,0)==AUX_PAGE&&!memcmp(header,aux_magic,sizeof(aux_magic));
    close(fd);
    fprintf(stderr,"GPU_LOAD_AUX_GUARD pass=%d blocks=%llu block_size=%u\n",ok,(unsigned long long)count,block);
    if(!ok){free(header);return 1;}
    /* Only the separately enumerated NS_06 with the exact size and dedicated
     * marker may be opened writable. Normal namespace 1 is never opened. */
    fd=open(path,O_RDWR|O_NOCTTY);
    fprintf(stderr,"GPU_LOAD_AUX_OPEN_RW fd=%d errno=%d\n",fd,fd<0?errno:0);
    if(fd<0){free(header);return 1;}
    unsigned char *again=NULL;
    if(posix_memalign((void **)&again,16384,AUX_PAGE)) {close(fd);free(header);return 1;}
    ok=pread(fd,again,AUX_PAGE,0)==AUX_PAGE&&!memcmp(again,header,AUX_PAGE);
    free(again);
    if(!ok){close(fd);free(header);return 1;}
    int nocache=fcntl(fd,F_NOCACHE,1);
    fprintf(stderr,"GPU_LOAD_AUX_NOCACHE result=%d errno=%d\n",nocache,nocache?errno:0);
    int result=nocache?1:aux_transfer(fd,header);
    close(fd);free(header);return result;
}
#endif
