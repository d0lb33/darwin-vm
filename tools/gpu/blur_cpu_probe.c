#include "blur_reference.h"
#include <stdio.h>
#include <time.h>
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec*1e-9;}
int main(void){unsigned w=256,h=256;size_t ni=(w+32)*(h+32)*8,nt=w*(h+32)*8,no=w*h*8;
 DVMHalf*in=malloc(ni),*tmp=malloc(nt),*a=malloc(no),*b=malloc(no);DVMBlurInput(in,w+32,h+32,1234);
 double t=now();DVMBlurCPU(in,tmp,a,w,h);double u=now();DVMBlurCPUHalf(in,tmp,b,w,h);double v=now();
 printf("float_us=%.3f half_us=%.3f exact=%d\n",(u-t)*1e6,(v-u)*1e6,!memcmp(a,b,no));return memcmp(a,b,no)!=0;
}
