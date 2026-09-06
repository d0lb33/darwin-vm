#pragma once
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
// Host-created paths only. The workload never supplies a pointer, GPA or path.
static void *DVMMapManaged(const uint8_t session[16],int *fdOut) {
    const char *ram=getenv("DVM_DRIVER_MANAGED_RAM"),*pages=getenv("DVM_DRIVER_MANAGED_PAGES");
    if(!ram||!pages)return NULL;
    const size_t count=759,length=count*16384;
    uint64_t record[4+759];struct stat st;
    int manifest=open(pages,O_RDONLY|O_NOFOLLOW);
    if(manifest<0)return NULL;
    BOOL valid=!fstat(manifest,&st)&&S_ISREG(st.st_mode)&&st.st_uid==getuid()&&!(st.st_mode&077)&&st.st_size==sizeof(record)&&pread(manifest,record,sizeof(record),0)==sizeof(record);
    close(manifest);
    if(!valid||memcmp(record,session,16)||record[2]!=length||record[3]!=count)return NULL;
    int fd=open(ram,O_RDWR|O_NOFOLLOW);
    if(fd<0)return NULL;
    if(fstat(fd,&st)||!S_ISREG(st.st_mode)||st.st_uid!=getuid()||(st.st_mode&077)||st.st_size!=0x300000000ull){close(fd);return NULL;}
    for(size_t i=0;i<count;i++) {
        if(record[4+i]%16384||record[4+i]>0x300000000ull-16384){close(fd);return NULL;}
        for(size_t j=0;j<i;j++)if(record[4+i]==record[4+j]){close(fd);return NULL;}
    }
    uint8_t *map=mmap(NULL,length,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);
    if(map==MAP_FAILED){close(fd);return NULL;}
    for(size_t i=0;i<count;i++)if(mmap(map+i*16384,16384,PROT_READ|PROT_WRITE,MAP_SHARED|MAP_FIXED,fd,record[4+i])!=map+i*16384){munmap(map,length);close(fd);return NULL;}
    *fdOut=fd;return map;
}
