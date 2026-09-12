#pragma once
#include <stddef.h>
typedef struct {size_t offset,length;} DVMDirtyRange;
// Conservative contiguous span of every changed byte. Scan the unchanged
// prefix and suffix exactly once. The guest runs under TCG, where libc memcmp
// may lower to translated SIMD and the former full comparison plus binary
// searches repeatedly visited a 256 KiB QuartzCore buffer each frame.
// Keep these loops scalar: finding the boundary already requires ordered
// loads, and at most n+1 bytes are inspected in total.
static inline DVMDirtyRange DVMFindDirtyRange(const void *a,const void *b,size_t n){
    const unsigned char *x=a,*y=b;size_t first=0,last=n;
#if defined(__clang__)
#pragma clang loop vectorize(disable) interleave(disable)
#endif
    while(first<n&&x[first]==y[first])first++;
    if(first==n)return (DVMDirtyRange){0,0};
#if defined(__clang__)
#pragma clang loop vectorize(disable) interleave(disable)
#endif
    while(last>first+1&&x[last-1]==y[last-1])last--;
    return (DVMDirtyRange){first,last-first};
}
