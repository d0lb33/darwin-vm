// Backend prerequisite for the next guest IOSurface integration. This optional
// extension is not advertised by the v5 guest capabilities yet.
static BOOL SharedRenderNumber(id value,uint64_t expected){uint64_t n;return Number(value,&n)&&n==expected;}
static NSDictionary *SharedRenderRequest(DVMHost *host,uint64_t seq,NSDictionary *r) {
    NSString *op=r[@"op"];
    if([op isEqual:@"sharedRenderCreate"]){
        // Explicit fixed-layout exception to ordinary copied texture budgets.
        // No offset/suballocation or arbitrary IOSurface import is implied.
        if(!SharedRenderNumber(r[@"width"],DVM_PRESENT_WIDTH)||!SharedRenderNumber(r[@"height"],DVM_PRESENT_HEIGHT)||
           !SharedRenderNumber(r[@"row"],DVM_PRESENT_ROW)||!SharedRenderNumber(r[@"format"],80)||!SharedRenderNumber(r[@"usage"],5)||
           !SharedRenderNumber(r[@"bytes"],DVM_PRESENT_BUFFER_BYTES))return HostError(seq,EINVAL,@"shared render fixed layout");
        if(host.residentBytes||host.textureBytes>kMaxTextures-DVM_PRESENT_BUFFER_BYTES)return HostError(seq,EBUSY,@"shared render allocation budget");
        for(DVMEntry *entry in host.entries.allValues)if(entry.sharedRender)return HostError(seq,EBUSY,@"shared render mapping already owned");
        DVMSharedRender *resource=DVMOpenSharedRender(host.device);
        if(!resource)return HostError(seq,EINVAL,@"shared render registration or native allocation");
        DVMEntry *entry;if(!Add(host,@"texture",resource.texture,&entry))return HostError(seq,ENOSPC,@"shared render object budget");
        entry.sharedRender=resource;entry.width=DVM_PRESENT_WIDTH;entry.height=DVM_PRESENT_HEIGHT;
        entry.row=DVM_PRESENT_ROW;entry.format=MTLPixelFormatBGRA8Unorm;entry.textureBytes=DVM_PRESENT_BUFFER_BYTES;
        host.textureBytes+=entry.textureBytes;
        return @{@"seq":@(seq),@"ok":@YES,@"handle":@(entry.handle),@"allocatedSize":@(entry.textureBytes),@"row":@(entry.row),@"shared":@YES};
    }
    DVMEntry *entry=Entry(host,r[@"handle"],@"texture");DVMSharedRender *resource=entry.sharedRender;uint64_t epoch;
    if(!resource)return HostError(seq,ENOENT,@"shared render handle");
    if(!Number(r[@"epoch"],&epoch)||!epoch)return HostError(seq,EINVAL,@"shared render epoch");
    if([op isEqual:@"sharedRenderAcquire"]){
        if(resource.state!=DVMSharedIdle||resource.epoch==UINT64_MAX||epoch!=resource.epoch+1)return HostError(seq,EBUSY,@"shared render acquire state/epoch");
        resource.epoch=epoch;resource.completedWrites=0;resource.state=DVMSharedAcquired;
    } else if([op isEqual:@"sharedRenderSeal"]){
        if(epoch!=resource.epoch||resource.state!=DVMSharedAcquired||!resource.completedWrites)return HostError(seq,EBUSY,@"shared render seal before GPU completion");
        resource.state=DVMSharedSealed;
    } else if([op isEqual:@"sharedRenderRetire"]){
        uint64_t swap;
        if(epoch!=resource.epoch||resource.state!=DVMSharedSealed||!Number(r[@"swap"],&swap)||swap>UINT32_MAX||
           !SharedRenderNumber(r[@"waitMode"],1)||!SharedRenderNumber(r[@"waitResult"],0))return HostError(seq,EINVAL,@"shared render retirement epoch/native wait report");
        resource.state=DVMSharedIdle;
    } else return HostError(seq,ENOSYS,@"unknown shared render operation");
    return @{@"seq":@(seq),@"ok":@YES,@"epoch":@(epoch),@"state":@(resource.state),@"completedWrites":@(resource.completedWrites)};
}
