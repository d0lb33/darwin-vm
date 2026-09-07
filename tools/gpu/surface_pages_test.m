// Host-only negative/alias controls for the same mapper used by the backend.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <assert.h>
#include "imported_pages_host.h"
int main(void){@autoreleasepool{
    char root[]="/tmp/dvm-surface-pages-XXXXXX";assert(mkdtemp(root));
    NSString *dir=@(root),*control=[dir stringByAppendingPathComponent:@"control"],*ram=[dir stringByAppendingPathComponent:@"ram"],*pages=[dir stringByAppendingPathComponent:@"pages"],*imports=[pages stringByAppendingString:@".imports"];
    assert(!mkdir(imports.fileSystemRepresentation,0700));
    setenv("DVM_DRIVER_PRESENT_RAM",control.fileSystemRepresentation,1);setenv("DVM_DRIVER_MANAGED_RAM",ram.fileSystemRepresentation,1);setenv("DVM_DRIVER_MANAGED_PAGES",pages.fileSystemRepresentation,1);
    uint8_t header[32]={0};DVMPagePut(header,UINT64_C(0x144564d31));memset(header+16,0x51,16);
    int c=open(control.fileSystemRepresentation,O_RDWR|O_CREAT|O_EXCL,0600);assert(c>=0&&!ftruncate(c,0x1000000)&&pwrite(c,header,32,0)==32);close(c);
    int f=open(ram.fileSystemRepresentation,O_RDWR|O_CREAT|O_EXCL,0600);assert(f>=0&&!ftruncate(f,DVM_SURFACE_DRAM_BYTES));
    uint8_t record[88]={0};DVMPagePut(record,DVM_SURFACE_MAGIC);DVMPagePut(record+8,1);memcpy(record+16,header+16,16);
    DVMPagePut(record+32,1);DVMPagePut(record+40,3*16384-17);DVMPagePut(record+48,7);DVMPagePut(record+56,3);
    DVMPagePut(record+64,2*16384);DVMPagePut(record+72,0);DVMPagePut(record+80,16384);
    NSString *manifest=[imports stringByAppendingPathComponent:@"0000000000000001.pages"];
    int p=open(manifest.fileSystemRepresentation,O_RDWR|O_CREAT|O_EXCL,0600);assert(p>=0&&write(p,record,88)==88);
    NSString *failure=nil;DVMImportedPages *m=DVMOpenImportedPages(1,&failure);assert(m&&m.offset==7&&m.length==49135&&m.span==49152);
    memset(m.mapping,0x39,m.span);uint8_t b=0;assert(pread(f,&b,1,2*16384)==1&&b==0x39);
    assert(pread(f,&b,1,0)==1&&b==0x39);assert(m.retire);assert(!m.retire);m=nil;
    assert(!DVMOpenImportedPages(1,&failure)&&[failure isEqual:@"retired surface ID"]);
    assert(!unlink([imports stringByAppendingPathComponent:@"0000000000000001.retired"].fileSystemRepresentation));
    record[16]^=1;assert(pwrite(p,record,88,0)==88);assert(!DVMOpenImportedPages(1,&failure)&&[failure isEqual:@"page registration session"]);record[16]^=1;
    DVMPagePut(record+72,2*16384);assert(pwrite(p,record,88,0)==88);assert(!DVMOpenImportedPages(1,&failure)&&[failure isEqual:@"page alignment/range/duplicate"]);
    DVMPagePut(record+72,DVM_SURFACE_DRAM_BYTES);assert(pwrite(p,record,88,0)==88);assert(!DVMOpenImportedPages(1,&failure));
    DVMPagePut(record+72,0);DVMPagePut(record+56,4098);assert(pwrite(p,record,88,0)==88);assert(!DVMOpenImportedPages(1,&failure));
    DVMPagePut(record+56,3);assert(pwrite(p,record,88,0)==88);assert(!fchmod(p,0644));assert(!DVMOpenImportedPages(1,&failure));
    close(p);close(f);assert([[NSFileManager defaultManager] removeItemAtPath:dir error:nil]);
    puts("PASS host mapper: partial byte range, scattered alias, retirement tombstone, replay, foreign session, duplicate/out-of-range pages, count and permissions");
}}
