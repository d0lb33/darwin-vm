// Host-only composition reference: preserve the exact guest's rasterized glyphs
// instead of asking the different host UIKit/font version to recreate them.
static void DVMUIKitReplaceGlyphs(UIView *root,CGColorSpaceRef space,NSString *directory){
    NSDictionary *metadata=[NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:[directory stringByAppendingPathComponent:@"inputs.json"]] options:0 error:nil];
    if(![metadata[@"scope"] isEqual:@"exact-guest-generated-glyph-inputs-not-rendered-output"]||[metadata[@"labels"] count]!=3)abort();
    for(NSDictionary *label in metadata[@"labels"]){
        NSString *name=label[@"name"];unsigned width=[label[@"width"] unsignedIntValue],height=[label[@"height"] unsignedIntValue];
        NSData *alpha=[NSData dataWithContentsOfFile:[directory stringByAppendingPathComponent:[name stringByAppendingString:@".a8"]]];
        NSArray *color=label[@"input_color_rgba"],*bounds=label[@"bounds"];
        if(!width||!height||width>1024||height>128||alpha.length!=width*height||color.count!=4||bounds.count!=4)abort();
        NSMutableData *pixels=[NSMutableData dataWithLength:width*height*4];
        const uint8_t *a=alpha.bytes;uint8_t *p=pixels.mutableBytes;
        for(unsigned i=0;i<width*height;i++){
            for(unsigned c=0;c<3;c++)p[i*4+c]=(uint8_t)lrint(a[i]*[color[2-c] doubleValue]);
            p[i*4+3]=a[i];
        }
        CGDataProviderRef provider=CGDataProviderCreateWithCFData((__bridge CFDataRef)pixels);
        CGImageRef image=CGImageCreate(width,height,8,32,width*4,space,kCGImageAlphaPremultipliedFirst|kCGBitmapByteOrder32Little,provider,NULL,YES,kCGRenderingIntentDefault);
        if(!image)abort();
        UIImageView *replacement=[[UIImageView alloc] initWithImage:[UIImage imageWithCGImage:image scale:2 orientation:UIImageOrientationUp]];
        CGImageRelease(image);CGDataProviderRelease(provider);
        UIView *parent=root;CGRect frame=CGRectMake([bounds[0] doubleValue],[bounds[1] doubleValue],width/2.,height/2.);
        if([name isEqual:@"title"]){[root.subviews[0] removeFromSuperview];[root insertSubview:replacement atIndex:0];}
        else if([name isEqual:@"caption"]){parent=root.subviews[1];[parent.subviews[1] removeFromSuperview];[parent addSubview:replacement];frame.origin.x-=20;frame.origin.y-=88;}
        else if([name isEqual:@"button"]){UIButton *button=(id)root.subviews[2];[button setTitle:@"" forState:UIControlStateNormal];[button addSubview:replacement];frame.origin.x-=40;frame.origin.y-=326;}
        else abort();
        replacement.frame=frame;
        fprintf(stderr,"UIKIT_HOST_GUEST_RASTER name=%s bytes=%lu width=%u height=%u scope=host-composition-reference\n",name.UTF8String,(unsigned long)alpha.length,width,height);
    }
    UIView *card=root.subviews[1],*button=root.subviews[2];
    NSArray<UIView *> *views=@[root,root.subviews[0],card,card.subviews[0],card.subviews[1],button,button.subviews.lastObject];
    NSArray *scales=metadata[@"layer_scales"],*curves=metadata[@"layer_corner_curves"];
    if(scales.count!=7||curves.count!=7)abort();
    for(unsigned i=0;i<7;i++){
        CGFloat scale=[scales[i] doubleValue];if(scale!=1&&scale!=2)abort();
        if(![curves[i] isEqual:@"circular"]&&![curves[i] isEqual:@"continuous"])abort();
        views[i].contentScaleFactor=scale;views[i].layer.cornerCurve=curves[i];
        fprintf(stderr,"UIKIT_HOST_GUEST_GEOMETRY index=%u scale=%.1f curve=%s scope=captured-layer-metadata\n",i,scale,[curves[i] UTF8String]);
    }
}
