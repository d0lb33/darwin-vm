#pragma once
#include "../../qemu-sptm/include/xnu/dvm_surface_registry.h"
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
static uint64_t DVMPageWord(const uint8_t *p){uint64_t v=0;for(unsigned i=0;i<8;i++)v|=(uint64_t)p[i]<<(8*i);return v;}
static void DVMPagePut(uint8_t *p,uint64_t v){for(unsigned i=0;i<8;i++)p[i]=v>>(8*i);}
@interface DVMImportedPages : NSObject
@property(nonatomic) void *mapping;
@property(nonatomic) NSUInteger span,length,offset;
@property(nonatomic) uint64_t resourceID;
@property(nonatomic) int ramFD,registryFD;
@property(nonatomic,strong) NSData *session;
@property(nonatomic,strong) id<MTLBuffer> buffer;
@property(nonatomic) BOOL retired;
@property(nonatomic) BOOL failed;
- (BOOL)retire;
@end
@implementation DVMImportedPages
- (instancetype)init {if((self=[super init])){_ramFD=-1;_registryFD=-1;}return self;}
- (BOOL)retire {
    if(_retired||_failed)return NO;
    // Caller has removed every texture alias and all native submissions have
    // completed. A failure never writes the kernel's retirement permission.
    _buffer=nil;
    if(_mapping&&munmap(_mapping,_span))return NO;
    _mapping=NULL;if(_ramFD>=0){close(_ramFD);_ramFD=-1;}
    char name[40];snprintf(name,sizeof(name),"%016llx.retired",(unsigned long long)_resourceID);
    uint8_t ack[32]={0};memcpy(ack,_session.bytes,16);DVMPagePut(ack+16,_resourceID);DVMPagePut(ack+24,DVM_SURFACE_RETIRED_MAGIC);
    int fd=openat(_registryFD,name,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0600);if(fd<0)return NO;
    BOOL ok=write(fd,ack,sizeof(ack))==sizeof(ack);close(fd);
    if(!ok){unlinkat(_registryFD,name,0);return NO;}
    close(_registryFD);_registryFD=-1;_retired=YES;return YES;
}
- (void)dealloc {
    // Unexpected teardown is NOT a completion fence. Keep mappings/fds alive
    // until worker process destruction; no tombstone means kernel stays pinned.
    // Normal release must explicitly retire after all aliases are gone.
}
@end
static int DVMOpenSurfaceRegistry(void){
    const char *pages=getenv("DVM_DRIVER_MANAGED_PAGES");if(!pages)return -1;
    NSString *path=[@(pages) stringByAppendingString:@".imports"];
    int fd=open(path.fileSystemRepresentation,O_RDONLY|O_DIRECTORY|O_NOFOLLOW);struct stat st;
    if(fd<0)return -1;
    if(fstat(fd,&st)||!S_ISDIR(st.st_mode)||st.st_uid!=getuid()||(st.st_mode&077)){close(fd);return -1;}
    return fd;
}
static DVMImportedPages *DVMOpenImportedPages(uint64_t id,NSString **failure){
    int directory=-1,manifest=-1,ram=-1;uint8_t header[64],session[32];struct stat st;
    uint8_t *mapping=NULL;NSUInteger span=0;NSString *reason=@"owned registry unavailable";
    NSData *pages=nil;NSMutableSet *seen=nil;DVMImportedPages *result=nil;
    char name[40];uint64_t length=0,offset=0,count=0;
    if(!id||id>UINT32_MAX)goto fail;
    directory=DVMOpenSurfaceRegistry();if(directory<0)goto fail;
    snprintf(name,sizeof(name),"%016llx.retired",(unsigned long long)id);
    // Tombstones survive slot reuse, so replay cannot remap an unpinned ID.
    if(!fstatat(directory,name,&st,AT_SYMLINK_NOFOLLOW)){reason=@"retired surface ID";goto fail;}
    if(errno!=ENOENT)goto fail;
    snprintf(name,sizeof(name),"%016llx.pages",(unsigned long long)id);
    manifest=openat(directory,name,O_RDONLY|O_NOFOLLOW);reason=@"registered page manifest";
    if(manifest<0||fstat(manifest,&st)||!S_ISREG(st.st_mode)||st.st_uid!=getuid()||(st.st_mode&077)||pread(manifest,header,64,0)!=64)goto fail;
    length=DVMPageWord(header+40);offset=DVMPageWord(header+48);count=DVMPageWord(header+56);
    if(DVMPageWord(header)!=DVM_SURFACE_MAGIC||DVMPageWord(header+8)!=1||DVMPageWord(header+32)!=id||
       !length||length>DVM_SURFACE_MAX_BYTES||offset>=DVM_SURFACE_PAGE||count!=(length+offset+DVM_SURFACE_PAGE-1)/DVM_SURFACE_PAGE||
       count>DVM_SURFACE_MAX_PAGES||st.st_size!=(off_t)(64+count*8))goto fail;
    reason=@"page registration session";
    const char *control=getenv("DVM_DRIVER_PRESENT_RAM");if(!control)goto fail;
    ram=open(control,O_RDONLY|O_NOFOLLOW);
    if(ram<0||fstat(ram,&st)||!S_ISREG(st.st_mode)||st.st_uid!=getuid()||(st.st_mode&077)||st.st_size!=0x1000000||pread(ram,session,32,0)!=32)goto fail;
    close(ram);ram=-1;
    if(DVMPageWord(session)!=UINT64_C(0x144564d31)||memcmp(header+16,session+16,16))goto fail;
    pages=[NSMutableData dataWithLength:count*8];
    if(pread(manifest,[(NSMutableData *)pages mutableBytes],count*8,64)!=(ssize_t)(count*8))goto fail;
    close(manifest);manifest=-1;
    const char *backing=getenv("DVM_DRIVER_MANAGED_RAM");if(!backing)goto fail;
    reason=@"registered DRAM backing";ram=open(backing,O_RDWR|O_NOFOLLOW);
    if(ram<0||fstat(ram,&st)||!S_ISREG(st.st_mode)||st.st_uid!=getuid()||(st.st_mode&077)||st.st_size!=DVM_SURFACE_DRAM_BYTES)goto fail;
    seen=[NSMutableSet set];
    for(NSUInteger i=0;i<count;i++){
        uint64_t off=DVMPageWord((const uint8_t *)pages.bytes+i*8);
        if(off%DVM_SURFACE_PAGE||off>DVM_SURFACE_DRAM_BYTES-DVM_SURFACE_PAGE||[seen containsObject:@(off)]){reason=@"page alignment/range/duplicate";goto fail;}
        [seen addObject:@(off)];
    }
    span=count*DVM_SURFACE_PAGE;mapping=mmap(NULL,span,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);
    if(mapping==MAP_FAILED){mapping=NULL;reason=@"VA reservation";goto fail;}
    for(NSUInteger i=0;i<count;i++)if(mmap(mapping+i*DVM_SURFACE_PAGE,DVM_SURFACE_PAGE,PROT_READ|PROT_WRITE,MAP_SHARED|MAP_FIXED,ram,DVMPageWord((const uint8_t *)pages.bytes+i*8))!=mapping+i*DVM_SURFACE_PAGE){reason=@"registered page map";goto fail;}
    result=[DVMImportedPages new];result.mapping=mapping;result.span=span;result.length=length;result.offset=offset;result.resourceID=id;
    result.ramFD=ram;result.registryFD=directory;result.session=[NSData dataWithBytes:header+16 length:16];return result;
fail:
    if(mapping)munmap(mapping,span);if(ram>=0)close(ram);if(manifest>=0)close(manifest);if(directory>=0)close(directory);
    if(failure)*failure=reason;return nil;
}
