#pragma once
/* CPU oracle for exact-guest compute_simd_blur_5 AIR: forward five-tap
 * half FMA, weights [1,4,6,4,1]/16. Each pass rounds to half after each FMA.
 * H reads a 32-pixel padded source; V reads H's extra 32 rows. No edge reads
 * outside allocations. Output is shifted by two pixels per axis by design. */
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
typedef _Float16 DVMHalf;
typedef float DVMFloat4 __attribute__((ext_vector_type(4)));
typedef _Float16 DVMHalf4 __attribute__((ext_vector_type(4)));
static inline void DVMBlurInput(DVMHalf *p,unsigned w,unsigned h,uint32_t nonce) {
    for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++)for(unsigned c=0;c<4;c++)
        {uint32_t v=x*0x45d9f3bu+y*0x119de1f3u+c*0x9e3779b9u+nonce;v^=v>>16;v*=0x45d9f3bu;v^=v>>16;
        p[((size_t)y*w+x)*4+c]=(_Float16)((v&63u)/64.0f); }
}
static inline void DVMBlurCPU(const DVMHalf *src,DVMHalf *tmp,DVMHalf *dst,unsigned w,unsigned h) {
    const float weights[5]={.0625f,.25f,.375f,.25f,.0625f};
    for(unsigned pass=0;pass<2;pass++) {
        unsigned rows=pass?h:h+32, stride=pass?w:w+32;
        const DVMHalf *in=pass?tmp:src;DVMHalf *out=pass?dst:tmp;
        for(unsigned y=0;y<rows;y++)for(unsigned x=0;x<w;x++) {
            DVMHalf4 acc={0,0,0,0};
            for(unsigned k=0;k<5;k++) {
                DVMHalf4 v;memcpy(&v,in+((size_t)(y+(pass?k:0))*stride+x+(pass?0:k))*4,8);
                DVMFloat4 a=__builtin_convertvector(acc,DVMFloat4),b=__builtin_convertvector(v,DVMFloat4);
                DVMFloat4 r=__builtin_elementwise_fma(b,(DVMFloat4){weights[k],weights[k],weights[k],weights[k]},a);
                acc=__builtin_convertvector(r,DVMHalf4);
            }
            memcpy(out+((size_t)y*w+x)*4,&acc,8);
        }
    }
}

/* Native FP16 control avoids the conversion-heavy float oracle. */
static inline __attribute__((target("fullfp16"))) void DVMBlurCPUHalf(const DVMHalf *src,DVMHalf *tmp,DVMHalf *dst,unsigned w,unsigned h) {
    const DVMHalf weights[5]={.0625f,.25f,.375f,.25f,.0625f};
    for(unsigned pass=0;pass<2;pass++) {
        unsigned rows=pass?h:h+32, stride=pass?w:w+32;
        const DVMHalf *in=pass?tmp:src;DVMHalf *out=pass?dst:tmp;
        for(unsigned y=0;y<rows;y++)for(unsigned x=0;x<w;x++) {
            DVMHalf4 acc={0,0,0,0};
            for(unsigned k=0;k<5;k++) {
                DVMHalf4 v;memcpy(&v,in+((size_t)(y+(pass?k:0))*stride+x+(pass?0:k))*4,8);
                acc=__builtin_elementwise_fma(v,(DVMHalf4){weights[k],weights[k],weights[k],weights[k]},acc);
            }
            memcpy(out+((size_t)y*w+x)*4,&acc,8);
        }
    }
}
