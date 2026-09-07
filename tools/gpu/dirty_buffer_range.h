#pragma once
#include <stddef.h>
#include <string.h>
typedef struct {size_t offset,length;} DVMDirtyRange;
// Conservative contiguous span of every changed byte. The two binary
// searches only compare shrinking halves, avoiding byte-wise guest scans.
// Callers still initialize the complete buffer and advance cache after ACK.
static inline DVMDirtyRange DVMFindDirtyRange(const void *a,const void *b,size_t n){
    if(!n||!memcmp(a,b,n))return (DVMDirtyRange){0,0};
    const unsigned char *x=a,*y=b;size_t lo=0,hi=n-1;
    while(lo<hi){size_t mid=lo+(hi-lo)/2;
        if(memcmp(x+lo,y+lo,mid-lo+1))hi=mid;else lo=mid+1;}
    size_t first=lo;hi=n-1;
    while(lo<hi){size_t mid=lo+(hi-lo+1)/2;
        if(memcmp(x+mid,y+mid,hi-mid+1))lo=mid;else hi=mid-1;}
    return (DVMDirtyRange){first,lo-first+1};
}
