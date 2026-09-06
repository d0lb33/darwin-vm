// Fixed resident blur primitive, opt-in mode-3 transport only. No addresses or
// file paths are accepted from the guest. Mapping and budget have one owner.
static NSDictionary *ResidentRequest(DVMHost *host,uint64_t seq,NSDictionary *r) {
    const char *path=getenv("DVM_DRIVER_PRESENT_RAM");
    if(!path||!*path)return HostError(seq,ENOTSUP,@"resident mapping is not enabled");
    NSString *op=r[@"op"];
    if([op isEqual:@"residentCreate"]) {
        uint64_t nonce;DVMEntry *library=Entry(host,r[@"library"],@"library");
        if(!library||!Number(r[@"nonce"],&nonce)||nonce>UINT32_MAX||host.residentBytes||host.textureBytes||host.entries.count!=1)return HostError(seq,EINVAL,@"resident setup ownership/budget");
        int fd=open(path,O_RDWR|O_NOFOLLOW);struct stat st;
        if(fd<0||fstat(fd,&st)||!S_ISREG(st.st_mode)||st.st_size!=0x1000000){if(fd>=0)close(fd);return HostError(seq,EINVAL,@"resident RAM extent");}
        void *map=mmap(NULL,0x1000000,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
        if(map==MAP_FAILED){close(fd);return HostError(seq,ENOMEM,@"resident map");}
        uint32_t magic;memcpy(&magic,map,4);
        if(magic!=0x44564d31){munmap(map,0x1000000);close(fd);return HostError(seq,EINVAL,@"resident RAM identity");}
        DVMResidentBlur *resource=[[DVMResidentBlur alloc]initWithDevice:host.device library:library.object destination:(uint8_t *)map+DVM_PRESENT_OFFSET nonce:(uint32_t)nonce];
        if(!resource){munmap(map,0x1000000);close(fd);return HostError(seq,EIO,@"resident Metal setup");}
        resource.ownedMap=map;resource.ownedFD=fd;resource.nonce=(uint32_t)nonce;
        DVMEntry *entry;if(!Add(host,@"resident",resource,&entry))return HostError(seq,ENOSPC,@"resident table");
        host.residentBytes=(1216ull*2592+1184ull*2592+1184ull*2560)*8+DVM_PRESENT_BUFFER_BYTES;
        return @{@"seq":@(seq),@"ok":@YES,@"handle":@(entry.handle),@"textures":@3,@"pipelines":@2,@"buffers":@1,@"resourceBytes":@(host.residentBytes)};
    }
    DVMEntry *entry=Entry(host,r[@"handle"],@"resident");if(!entry)return HostError(seq,ENOENT,@"resident handle");
    DVMResidentBlur *resource=entry.object;
    if([op isEqual:@"residentDraw"]) {
        uint64_t frame;if(!Number(r[@"frame"],&frame)||frame!=resource.lastFrame+1||frame>33)return HostError(seq,EINVAL,@"resident frame sequence");
        id<MTLCommandBuffer>cb=[resource encodeFrame:(uint32_t)frame];[cb commit];[cb waitUntilCompleted];
        if(cb.status!=MTLCommandBufferStatusCompleted)return NSErrorReply(seq,cb.error,@"resident GPU error");
        resource.lastFrame=(uint32_t)frame;host.submissions++;
        return @{@"seq":@(seq),@"ok":@YES,@"status":@4,@"frame":@(frame),@"gpu_us":@((cb.GPUEndTime-cb.GPUStartTime)*1e6),@"dispatches":@3};
    }
    if([op isEqual:@"residentVerify"]) {
        if(resource.lastFrame!=33)return HostError(seq,EINVAL,@"resident batch incomplete");
        const uint8_t *pixels=(const uint8_t *)resource.ownedMap+DVM_PRESENT_OFFSET;
        unsigned bad=DVMVerifyPresented(pixels,resource.nonce,33);
        if(bad)return HostError(seq,EILSEQ,@"resident final pixels differ");
        return @{@"seq":@(seq),@"ok":@YES,@"verified":@YES,@"bad_pixels":@0,@"frame":@33};
    }
    return HostError(seq,ENOSYS,@"unknown resident operation");
}
