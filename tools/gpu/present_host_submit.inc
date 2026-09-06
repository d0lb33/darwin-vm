// Fixed resident blur primitive, opt-in mode-3 transport only. No addresses or
// file paths are accepted from the guest. Mapping and budget have one owner.
static NSDictionary *ResidentRequest(DVMHost *host,uint64_t seq,NSDictionary *r) {
    const char *path=getenv("DVM_DRIVER_PRESENT_RAM");
    if(!path||!*path)return HostError(seq,ENOTSUP,@"resident mapping is not enabled");
    NSString *op=r[@"op"];
    if([op isEqual:@"residentCreate"]) {
        uint64_t frames=33;
        if(r[@"frames"]&&(!Number(r[@"frames"],&frames)||frames<2||frames>DVM_PRESENT_MAX_FRAMES))return HostError(seq,EINVAL,@"resident frame budget");
        uint64_t nonce;DVMEntry *library=Entry(host,r[@"library"],@"library");
        if(!library||!Number(r[@"nonce"],&nonce)||nonce>UINT32_MAX||host.residentBytes||host.textureBytes||host.entries.count!=1)return HostError(seq,EINVAL,@"resident setup ownership/budget");
        int fd=open(path,O_RDWR|O_NOFOLLOW);struct stat st;
        if(fd<0||fstat(fd,&st)||!S_ISREG(st.st_mode)||st.st_size!=0x1000000){if(fd>=0)close(fd);return HostError(seq,EINVAL,@"resident RAM extent");}
        void *map=mmap(NULL,0x1000000,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
        if(map==MAP_FAILED){close(fd);return HostError(seq,ENOMEM,@"resident map");}
        uint32_t magic;memcpy(&magic,map,4);
        if(magic!=0x44564d31){munmap(map,0x1000000);close(fd);return HostError(seq,EINVAL,@"resident RAM identity");}
        BOOL managed=getenv("DVM_DRIVER_MANAGED_RAM")!=NULL;
        size_t mapLength=0x1000000;void *pixels=(uint8_t *)map+DVM_PRESENT_OFFSET;
        if(managed) {
            int managedFD=-1;void *managedMap=DVMMapManaged((uint8_t *)map+16,&managedFD);
            munmap(map,mapLength);close(fd);
            if(!managedMap)return HostError(seq,EINVAL,@"managed registration/mapping contract");
            map=managedMap;fd=managedFD;mapLength=DVM_PRESENT_BUFFER_BYTES;pixels=map;
            // Two-way alias prerequisite: guest wrote these words before create.
            if(((uint32_t *)pixels)[0]!=0x44564d41||((uint32_t *)pixels)[1]!=0x44564d42){munmap(map,mapLength);close(fd);return HostError(seq,EILSEQ,@"managed guest-to-host alias");}
            ((uint32_t *)pixels)[2]=0x44564d43;
            memset((uint8_t *)pixels+DVM_PRESENT_BYTES,0xcd,DVM_PRESENT_BUFFER_BYTES-DVM_PRESENT_BYTES);
        }
        DVMResidentBlur *resource=[[DVMResidentBlur alloc]initWithDevice:host.device library:library.object destination:pixels nonce:(uint32_t)nonce];
        if(!resource){munmap(map,mapLength);close(fd);return HostError(seq,EIO,@"resident Metal setup");}
        resource.ownedMap=map;resource.ownedFD=fd;resource.ownedLength=mapLength;resource.managed=managed;resource.nonce=(uint32_t)nonce;
        resource.frameCount=(uint32_t)frames;
        const char *delay=getenv("DVM_DRIVER_MANAGED_DELAY_US");
        if(managed&&delay) {
            char *tail=NULL;unsigned long value=strtoul(delay,&tail,10);
            if(!*delay||*tail||value>250000)return HostError(seq,EINVAL,@"managed delay limit");
            resource.completionDelayUS=(unsigned)value;
        }
        DVMEntry *entry;if(!Add(host,@"resident",resource,&entry))return HostError(seq,ENOSPC,@"resident table");
        host.residentBytes=(1216ull*2592+1184ull*2592+1184ull*2560)*8+DVM_PRESENT_BUFFER_BYTES;
        return @{@"seq":@(seq),@"ok":@YES,@"handle":@(entry.handle),@"textures":@3,@"pipelines":@2,@"buffers":@1,@"resourceBytes":@(host.residentBytes)};
    }
    DVMEntry *entry=Entry(host,r[@"handle"],@"resident");if(!entry)return HostError(seq,ENOENT,@"resident handle");
    DVMResidentBlur *resource=entry.object;
    if([op isEqual:@"residentDraw"]) {
        if(resource.displayPending)return HostError(seq,EBUSY,@"managed surface awaits display retirement");
        uint64_t frame;if(!Number(r[@"frame"],&frame)||frame!=resource.lastFrame+1||frame>resource.frameCount)return HostError(seq,EINVAL,@"resident frame sequence");
        id<MTLCommandBuffer>cb=[resource encodeFrame:(uint32_t)frame];
        if(!cb)return HostError(seq,EIO,@"resident command allocation");
        [cb commit];[cb waitUntilCompleted];
        if(cb.status!=MTLCommandBufferStatusCompleted)return NSErrorReply(seq,cb.error,@"resident GPU error");
        resource.lastFrame=(uint32_t)frame;host.submissions++;
        resource.displayPending=resource.managed;
        return @{@"seq":@(seq),@"ok":@YES,@"status":@4,@"frame":@(frame),@"gpu_us":@((cb.GPUEndTime-cb.GPUStartTime)*1e6),@"dispatches":@3};
    }
    if([op isEqual:@"residentRetire"]) {
        uint64_t frame;
        if(!resource.managed||!resource.displayPending||!Number(r[@"frame"],&frame)||frame!=resource.lastFrame)return HostError(seq,EINVAL,@"managed retirement frame/state");
        resource.displayPending=NO;
        return @{@"seq":@(seq),@"ok":@YES,@"frame":@(frame)};
    }
    if([op isEqual:@"residentVerify"]) {
        if(resource.lastFrame!=resource.frameCount||resource.displayPending)return HostError(seq,EINVAL,@"resident batch incomplete");
        const uint8_t *pixels=resource.pixels;
        if(resource.managed)for(size_t i=DVM_PRESENT_BYTES;i<DVM_PRESENT_BUFFER_BYTES;i++)if(pixels[i]!=0xcd)return HostError(seq,EILSEQ,@"managed tail guard");
        unsigned bad=DVMVerifyPresented(pixels,resource.nonce,resource.frameCount);
        if(bad)return HostError(seq,EILSEQ,@"resident final pixels differ");
        return @{@"seq":@(seq),@"ok":@YES,@"verified":@YES,@"bad_pixels":@0,@"frame":@(resource.frameCount)};
    }
    return HostError(seq,ENOSYS,@"unknown resident operation");
}
