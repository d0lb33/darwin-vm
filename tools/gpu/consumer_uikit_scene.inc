// Shared test scene definition for exact-guest and native-host comparisons.
#import <UIKit/UIView.h>
#import <UIKit/UILabel.h>
#import <UIKit/UIButton.h>
#import <UIKit/UIImageView.h>
#import <UIKit/UIColor.h>
#import <UIKit/UIFont.h>
#import <UIKit/UIImage.h>
static UIView *DVMUIKitScene(CGColorSpaceRef space,unsigned width,unsigned height){
    UIView *view=[[UIView alloc] initWithFrame:CGRectMake(0,0,width,height)];
    view.backgroundColor=[UIColor colorWithRed:.08 green:.12 blue:.20 alpha:1];
    UILabel *title=[[UILabel alloc] initWithFrame:CGRectMake(20,22,280,46)];
    title.text=@"UIKit on host Metal";title.font=[UIFont boldSystemFontOfSize:24];
    title.textColor=[UIColor whiteColor];[view addSubview:title];
    UIView *card=[[UIView alloc] initWithFrame:CGRectMake(20,88,280,200)];
    card.backgroundColor=[UIColor colorWithRed:.15 green:.30 blue:.65 alpha:1];
    card.layer.cornerRadius=24;card.clipsToBounds=YES;[view addSubview:card];
    uint32_t imagePixels[16*16];for(unsigned y=0;y<16;y++)for(unsigned x=0;x<16;x++)
        imagePixels[y*16+x]=((x/4+y/4)%2)?0xff20d0f0:0xfff08020;
    CGContextRef imageContext=CGBitmapContextCreate(imagePixels,16,16,8,64,space,kCGImageAlphaPremultipliedFirst|kCGBitmapByteOrder32Little);
    CGImageRef image=CGBitmapContextCreateImage(imageContext);
    UIImageView *picture=[[UIImageView alloc] initWithFrame:CGRectMake(-12,20,304,128)];
    picture.image=[UIImage imageWithCGImage:image];[card addSubview:picture];
    CGImageRelease(image);CGContextRelease(imageContext);
    UILabel *caption=[[UILabel alloc] initWithFrame:CGRectMake(16,154,252,30)];
    caption.text=@"Text, image and rounded clipping";caption.font=[UIFont systemFontOfSize:14];
    caption.textColor=[UIColor whiteColor];[card addSubview:caption];
    UIButton *button=[UIButton buttonWithType:UIButtonTypeSystem];
    button.frame=CGRectMake(40,326,240,56);[button setTitle:@"Render another frame" forState:UIControlStateNormal];
    button.backgroundColor=[UIColor colorWithRed:.1 green:.6 blue:.4 alpha:1];
    button.layer.cornerRadius=16;[view addSubview:button];
    return view;
}
