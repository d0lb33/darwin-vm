// Test-only public UIWindow attachment. Log actual application/scene state;
// attachment alone must not be reported as a normal connected app lifecycle.
// Xcode 27 beta UITextInput declares watchOS both available/unavailable.
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wavailability"
#import <UIKit/UIWindow.h>
#import <UIKit/UIWindowScene.h>
#import <UIKit/UIViewController.h>
#import <UIKit/UIApplication.h>
#import <UIKit/UIAccessibility.h>
#pragma clang diagnostic pop
static inline UIWindow *DVMUIKitAttachWindow(UIView *view,UIWindowScene *scene){
    fprintf(stderr,"GPU_LOAD_UIKIT_WINDOW stage=begin application=%u\n",[UIApplication sharedApplication]!=nil);
    UIWindow *window=scene?[[UIWindow alloc] initWithWindowScene:scene]:[[UIWindow alloc] initWithFrame:view.bounds];
    window.frame=view.bounds;
    UIViewController *controller=[UIViewController new];controller.view=view;
    window.rootViewController=controller;
    [window makeKeyAndVisible];
    fprintf(stderr,"GPU_LOAD_UIKIT_WINDOW_TRAITS application_state=%ld scene_state=%ld style=%ld contrast=%ld reduce_transparency=%u reduce_motion=%u\n",
        [UIApplication sharedApplication]?(long)[UIApplication sharedApplication].applicationState:-99L,
        window.windowScene?(long)window.windowScene.activationState:-99L,(long)view.traitCollection.userInterfaceStyle,
        (long)view.traitCollection.accessibilityContrast,UIAccessibilityIsReduceTransparencyEnabled(),UIAccessibilityIsReduceMotionEnabled());
    fprintf(stderr,"GPU_LOAD_UIKIT_WINDOW stage=attached attached=%u scene=%u key=%u hidden=%u width=%.0f height=%.0f\n",
        view.window==window,window.windowScene!=nil,window.isKeyWindow,window.hidden,view.bounds.size.width,view.bounds.size.height);
    return window;
}
