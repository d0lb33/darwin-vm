#include "metal_library_slice.h"
#include <assert.h>
#include <stdio.h>
static void be(uint8_t *p,uint32_t n){for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(n>>(24-8*i));}
static void mtlb(uint8_t *p,size_t n){memset(p,0,n);memcpy(p,"MTLB",4);for(unsigned i=0;i<8;i++)p[16+i]=(uint8_t)((uint64_t)n>>(8*i));}
int main(void){
    uint8_t data[224]={0};size_t off=0,n=0;
    mtlb(data,88);assert(!DVMSelectLibrarySlice(data,88,&off,&n)&&off==0&&n==88);
    assert(DVMSelectLibrarySlice(data,87,&off,&n));
    data[16]++;assert(DVMSelectLibrarySlice(data,88,&off,&n));
    memset(data,0,sizeof(data));be(data,0xcafebabe);be(data+4,2);
    be(data+16,48);be(data+20,88);be(data+24,3);
    be(data+36,136);be(data+40,88);be(data+44,3);
    mtlb(data+48,88);memcpy(data+136,"ARCH",4);
    assert(!DVMSelectLibrarySlice(data,sizeof(data),&off,&n)&&off==48&&n==88);
    mtlb(data+136,88);assert(DVMSelectLibrarySlice(data,sizeof(data),&off,&n));
    memcpy(data+48,"ARCH",4);assert(!DVMSelectLibrarySlice(data,sizeof(data),&off,&n)&&off==136);
    be(data+36,128);assert(DVMSelectLibrarySlice(data,sizeof(data),&off,&n));
    be(data+36,137);assert(DVMSelectLibrarySlice(data,sizeof(data),&off,&n));
    be(data+36,224);assert(DVMSelectLibrarySlice(data,sizeof(data),&off,&n));
    be(data+4,129);assert(DVMSelectLibrarySlice(data,sizeof(data),&off,&n));
    assert(DVMSelectLibrarySlice(data,7,&off,&n));
    puts("MTLB slice selection and malformed-container controls passed");
}
