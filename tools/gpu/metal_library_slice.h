#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

static inline uint32_t DVMLibraryBE32(const uint8_t *p) {
    return (uint32_t)p[0]<<24|(uint32_t)p[1]<<16|(uint32_t)p[2]<<8|p[3];
}
static inline uint64_t DVMLibraryLE64(const uint8_t *p) {
    uint64_t n=0;for(unsigned i=0;i<8;i++)n|=(uint64_t)p[i]<<(8*i);return n;
}
// Select unchanged MTLB bytes, never a slice by a known shader's byte count.
// FAT_MAGIC/fat_arch use the standard big-endian Mach-O container layout.
// MTLB's declared file length is at +16 in both observed guest/host resources;
// the native Metal loader remains responsible for version/target compatibility.
// GPU Mach-O archives are not executable AIR libraries. Ambiguous multiple
// MTLB slices require a separately established selection contract.
static inline const char *DVMSelectLibrarySlice(const uint8_t *p,size_t n,size_t *offset,size_t *length) {
    if(!p||n<4)return "library header truncated";
    *offset=0;*length=n;
    if(!memcmp(p,"\xca\xfe\xba\xbe",4)) {
        if(n<8)return "fat library header truncated";
        uint32_t count=DVMLibraryBE32(p+4);
        if(!count||count>128||n<8+(size_t)count*20)return "fat library table bounds";
        size_t table=8+(size_t)count*20;unsigned found=0;
        for(uint32_t i=0;i<count;i++) {
            const uint8_t *a=p+8+i*20;
            size_t off=DVMLibraryBE32(a+8),size=DVMLibraryBE32(a+12);
            uint32_t align=DVMLibraryBE32(a+16);
            if(off<table||!size||off>n||size>n-off||align>31||(off&(((size_t)1<<align)-1)))
                return "fat library slice bounds/alignment";
            for(uint32_t j=0;j<i;j++) {
                size_t other=DVMLibraryBE32(p+8+j*20+8),bytes=DVMLibraryBE32(p+8+j*20+12);
                if(off<other+bytes&&other<off+size)return "fat library overlapping slices";
            }
            if(size>=4&&!memcmp(p+off,"MTLB",4)){*offset=off;*length=size;found++;}
        }
        if(found!=1)return found?"ambiguous MTLB slices":"fat library contains no MTLB slice";
    }
    const uint8_t *slice=p+*offset;
    if(*length<88||memcmp(slice,"MTLB",4)||DVMLibraryLE64(slice+16)!=*length)
        return "MTLB header/declared length";
    if(*length>12*1024*1024)return "MTLB exceeds forwarding library limit";
    return NULL;
}
