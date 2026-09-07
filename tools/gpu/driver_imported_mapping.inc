// Kernel selectors pin the actual calling task's IOSurface allocation. Neither
// physical addresses nor page lists are supplied by this process.
@interface DVMImportedMapping : NSObject <DVMMetalImportedMapping>
@property(nonatomic,strong) Namespace *owner;
@property(nonatomic,strong) id surfaceObject;
@property(nonatomic) uint64_t resourceID;
@property(nonatomic) BOOL retired;
@end
@implementation DVMImportedMapping
- (NSUInteger)mappingVersion {return 2;}
- (IOSurfaceRef)iosurface {return (__bridge IOSurfaceRef)_surfaceObject;}
- (void *)bytes {return IOSurfaceGetBaseAddress(self.iosurface);}
- (NSUInteger)length {return IOSurfaceGetAllocSize(self.iosurface);}
- (BOOL)retire {
    if(_retired)return NO;
    uint64_t input[3]={1,_resourceID,0},output[4]={0};uint32_t count=4;
    kern_return_t kr=IOConnectCallScalarMethod(_owner.client,0x44565302,input,3,output,&count);
    BOOL ok=!kr&&count==4&&output[0]==1&&output[1]==_resourceID&&!output[2]&&!output[3];
    DVMReport(stderr,"GPU_LOAD_SURFACE_RETIRE id=%llu kr=%x count=%u status=%llu complete=%llu ok=%d\n",_resourceID,kr,count,output[2],output[3],ok);
    if(ok){_retired=YES;IOSurfaceDecrementUseCount(self.iosurface);}return ok;
}
// No dealloc unpin. Uncertain ownership is quarantined until VM destruction.
@end
static DVMMetalSurfaceProvider DVMImportedSurfaceProvider(Namespace *ns){
    NSMapTable *cache=[NSMapTable strongToWeakObjectsMapTable];
    return ^id<DVMMetalImportedMapping>(IOSurfaceRef s,NSError **e){
        NSNumber *key=@(IOSurfaceGetID(s));DVMImportedMapping *m=[cache objectForKey:key];
        if(m&&!m.retired)return m;
        m=[DVMImportedMapping new];m.owner=ns;m.surfaceObject=(__bridge id)s;
        // Native usage count protects a pool consumer from reusing this surface
        // while our texture aliases survive. It is not a GPU or DCP fence.
        IOSurfaceIncrementUseCount(s);
        if(!IOSurfaceIsInUse(s)){ns.failed=YES;if(e)*e=[NSError errorWithDomain:@"DVMSurfaceRegistration" code:kIOReturnNotReady userInfo:@{NSLocalizedDescriptionKey:@"IOSurface usage count contract"}];return nil;}
        uint64_t input[3]={1,(uintptr_t)m.bytes,m.length},output[4]={0};uint32_t count=4;
        kern_return_t kr=IOConnectCallScalarMethod(ns.client,0x44565301,input,3,output,&count);
        NSUInteger span=(((uintptr_t)m.bytes&16383)+m.length+16383)&~16383ul;
        BOOL ok=!kr&&count==4&&output[0]==1&&output[1]&&output[2]==m.length&&output[3]==span;
        DVMReport(stderr,"GPU_LOAD_SURFACE_REGISTER surface=%u kr=%x count=%u id=%llu bytes=%llu span=%llu ok=%d\n",IOSurfaceGetID(s),kr,count,output[1],output[2],output[3],ok);
        if(!ok){ns.failed=YES;if(e)*e=[NSError errorWithDomain:@"DVMSurfaceRegistration" code:kr?:kIOReturnBadArgument userInfo:@{NSLocalizedDescriptionKey:@"kernel registration contract"}];return nil;}
        m.resourceID=output[1];[cache setObject:m forKey:key];return m;
    };
}
