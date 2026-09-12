#include "dirty_buffer_range.h"
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static uint32_t state=1;
static uint32_t next(void){state=state*1664525u+1013904223u;return state;}
int main(void){
    static unsigned char storageA[262144+8],storageB[262144+8];
    for(unsigned trial=0;trial<4000;trial++){
        size_t skew=trial%8,n=trial<16?trial:1+next()%262144;
        unsigned char *a=storageA+skew,*b=storageB+skew;
        for(size_t i=0;i<n;i++)a[i]=(unsigned char)next();
        memcpy(b,a,n);
        if(n){if(trial%5==0)b[0]^=1;else if(trial%5==1)b[n-1]^=1;
            else if(trial%5==2)b[n/2]^=1;
            else if(trial%5==3)for(unsigned j=0;j<8;j++)b[next()%n]^=1;}
        size_t first=n,last=0;
        for(size_t i=0;i<n;i++)if(a[i]!=b[i]){if(first==n)first=i;last=i+1;}
        DVMDirtyRange r=DVMFindDirtyRange(a,b,n);
        assert(r.length==(first==n?0:last-first));assert(!r.length||r.offset==first);
        memcpy(a+r.offset,b+r.offset,r.length);assert(!memcmp(a,b,n));
    }
    puts("PASS 4000 dirty-span cases: 256 KiB extents, unaligned starts, unchanged, endpoints, middle, holes and replayed bytes");return 0;
}
