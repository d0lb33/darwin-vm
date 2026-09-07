// Shared test layout for native host references and exact guest presentation.
// Scaling is performed by QuartzCore, not a CPU image resize or backend rule.
static CALayer *DVMUIKitDisplayScene(UIView *view,CGColorSpaceRef space){
    CALayer *root=[CALayer layer];root.frame=CGRectMake(0,0,1179,2556);
    CGFloat components[]={.08,.12,.20,1};
    CGColorRef color=CGColorCreate(space,components);root.backgroundColor=color;CGColorRelease(color);
    view.layer.anchorPoint=CGPointZero;view.layer.position=CGPointMake(109,558);
    view.layer.transform=CATransform3DMakeScale(3,3,1);
    [root addSublayer:view.layer];
    return root;
}
static inline void DVMUIKitDisplayUpdate(UIView *view,unsigned frame){
    UIView *card=view.subviews[1];
    card.frame=CGRectMake(frame%2?20:28,88,280,200);
    card.alpha=frame%2?1:.7;
}
