#pragma once
#include "present_layout.h"
#include "blur_reference.h"
static inline unsigned DVMVerifyPresented(const uint8_t *pixels,uint32_t nonce,uint32_t frame) {
    DVMHalf *input=malloc((size_t)1216*2592*8),*tmp=malloc((size_t)1184*2592*8),*output=malloc((size_t)1184*2560*8);
    if(!input||!tmp||!output){free(input);free(tmp);free(output);return UINT32_MAX;}
    DVMBlurInput(input,1216,2592,nonce);
#ifdef DVM_DRIVER_PRESENT
    DVMBlurCPUHalf(input,tmp,output,1184,2560);
#else
    DVMBlurCPU(input,tmp,output,1184,2560);
#endif
    unsigned bad=0;
    for(unsigned y=0;y<DVM_PRESENT_HEIGHT;y++)for(unsigned x=0;x<DVM_PRESENT_WIDTH;x++) {
        uint32_t want=0;for(unsigned c=0;c<4;c++)want|=(uint32_t)(uint8_t)((float)output[((size_t)y*1184+x)*4+(c<3?2-c:c)]*255.0f+.5f)<<(c*8);
        if(!y&&x<4)want=x==0?0xff44564d:x==1?0xff505253:x==2?0xff424c52:0xff000000|frame;
        uint32_t got;memcpy(&got,pixels+(size_t)y*DVM_PRESENT_ROW+x*4,4);bad+=got!=want;
    }
    free(input);free(tmp);free(output);return bad;
}
