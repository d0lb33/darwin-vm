// Real UIKit effects over the existing checker. No custom blur shader or
// imitation glass: missing backdrop work is an experimental result.
#import <UIKit/UIVisualEffectView.h>
#import <UIKit/UIBlurEffect.h>
#import <UIKit/UIGlassEffect.h>
static inline void DVMUIKitAddEffect(UIView *root,unsigned kind){
    UIVisualEffect *effect=nil;
    if(kind==1)effect=[UIBlurEffect effectWithStyle:UIBlurEffectStyleSystemMaterial];
    else if(kind==2)effect=[UIGlassEffect effectWithStyle:UIGlassEffectStyleRegular];
    else if(kind)abort();
    if(!effect)return;
    UIVisualEffectView *overlay=[[UIVisualEffectView alloc] initWithEffect:effect];
    overlay.frame=CGRectMake(40,128,240,80);
    overlay.layer.cornerRadius=20;overlay.clipsToBounds=YES;
    [root addSubview:overlay];
}
