// Test-only layer definitions, also used by the native-host rehearsal.
#ifndef DVM_CA_SCENE
#define DVM_CA_SCENE 0
#endif
static CALayer *sharedSceneCreate(CALayer *root,CGColorSpaceRef space){
    if(!DVM_CA_SCENE)return nil;
    sharedColor(root,space,0xff0000ff);
    CALayer *moving=[CALayer layer],*front=[CALayer layer];
    moving.frame=CGRectMake(0,0,160,DVM_PRESENT_HEIGHT);
    front.frame=CGRectMake(280,0,80,DVM_PRESENT_HEIGHT);
    sharedColor(moving,space,0xffff0000);sharedColor(front,space,0xff00ff00);
    if(DVM_CA_SCENE==1)moving.opacity=.5;
    if(DVM_CA_SCENE==2){
        CALayer *clip=[CALayer layer];clip.frame=CGRectMake(80,0,DVM_PRESENT_WIDTH-160,DVM_PRESENT_HEIGHT);
        clip.masksToBounds=YES;[root addSublayer:clip];[clip addSublayer:moving];
        moving.anchorPoint=CGPointZero;moving.bounds=CGRectMake(0,0,80,DVM_PRESENT_HEIGHT);
        moving.transform=CATransform3DMakeScale(2,1,1);
    }else [root addSublayer:moving];
    if(DVM_CA_SCENE==3){
        const uint8_t bytes[]={255,0,0,255,0,255,0,255};
        CFDataRef data=CFDataCreate(NULL,bytes,sizeof(bytes));
        CGDataProviderRef provider=CGDataProviderCreateWithCFData(data);
        CGImageRef image=CGImageCreate(2,1,8,32,8,space,kCGBitmapByteOrder32Big|kCGImageAlphaPremultipliedLast,provider,NULL,false,kCGRenderingIntentDefault);
        if(!image)fail("shared-image");
        moving.backgroundColor=NULL;moving.contents=(__bridge id)image;
        moving.magnificationFilter=kCAFilterNearest;moving.minificationFilter=kCAFilterNearest;
        CGImageRelease(image);CGDataProviderRelease(provider);CFRelease(data);
    }
    [root addSublayer:front];return moving;
}
static void sharedSceneUpdate(CALayer *root,CALayer *moving,CGColorSpaceRef space,unsigned frame){
    if(!DVM_CA_SCENE){sharedColor(root,space,frame%3==1?0xffff0000:frame%3==2?0xff00ff00:0xff0000ff);return;}
    unsigned x=(frame*17)%(DVM_PRESENT_WIDTH-159);
    if(DVM_CA_SCENE==2)moving.position=CGPointMake((int)x-80,0);
    else moving.frame=CGRectMake(x,0,160,DVM_PRESENT_HEIGHT);
}
