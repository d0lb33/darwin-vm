/* Native cost of the guest transport's per-request encoding, for comparison
 * with the guest-side gaps measured under TCG. crc() is byte-identical to
 * tools/gpu/system_bootstrap.m:9-12 (bit-serial CRC-32). */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static uint32_t crc(const void *data,size_t n) {
    uint32_t c=~0u;const uint8_t *p=data;
    while(n--){c^=*p++;for(unsigned j=0;j<8;j++)c=(c>>1)^((0u-(c&1))&0xedb88320u);}return ~c;
}
static uint32_t table[256];
static void init_table(void){for(unsigned i=0;i<256;i++){uint32_t c=i;for(int j=0;j<8;j++)c=(c>>1)^((0u-(c&1))&0xedb88320u);table[i]=c;}}
static uint32_t crc_table(const void *data,size_t n){uint32_t c=~0u;const uint8_t *p=data;while(n--)c=table[(c^*p++)&0xff]^(c>>8);return ~c;}
static const char b64[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
static size_t base64(const uint8_t *in,size_t n,char *out){size_t o=0;for(size_t i=0;i<n;i+=3){uint32_t v=in[i]<<16|(i+1<n?in[i+1]<<8:0)|(i+2<n?in[i+2]:0);out[o++]=b64[v>>18&63];out[o++]=b64[v>>12&63];out[o++]=i+1<n?b64[v>>6&63]:'=';out[o++]=i+2<n?b64[v&63]:'=';}return o;}
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
int main(int argc,char**argv){
    size_t sizes[]={64,1305,32768,44000,2663424,3551232};
    init_table();
    uint8_t *buf=malloc(8<<20);char *enc=malloc(12<<20);for(size_t i=0;i<(8<<20);i++)buf[i]=(uint8_t)(i*2654435761u>>13);
    volatile uint32_t sink=0;
    printf("%10s %14s %14s %14s\n","bytes","bitcrc_us","tablecrc_us","base64_us");
    for(unsigned s=0;s<sizeof sizes/sizeof *sizes;s++){
        size_t n=sizes[s];int reps=n<100000?200:5;double best[3]={1e9,1e9,1e9};
        for(int r=0;r<reps;r++){double t=now();sink+=crc(buf,n);double d=now()-t;if(d<best[0])best[0]=d;
            t=now();sink+=crc_table(buf,n);d=now()-t;if(d<best[1])best[1]=d;
            t=now();sink+=base64(buf,n,enc);d=now()-t;if(d<best[2])best[2]=d;}
        printf("%10zu %14.1f %14.1f %14.1f   (bit-serial: %.1f ns/byte)\n",n,best[0]*1e6,best[1]*1e6,best[2]*1e6,best[0]*1e9/n);
    }
    return sink==1;
}
