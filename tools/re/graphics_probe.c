/*
 * One-shot native bitmap diagnostic for the iOS System image.
 *
 * This is observation only.  It creates one bounded 64x64 BGRA8 CoreGraphics
 * bitmap, asks the exported IconFoundation IFGraphicsContext APIs to make the
 * equivalent contexts, and logs whether each context and image exists.
 *
 * ABI evidence, iOS 24A5430a:
 * - IconFoundation +[IFGraphicsContext bitmapContextWithSize:scale:pixelFormat:]
 *   at shared-cache VA 0x22ff42fd4 receives CGSize in d0/d1, scale in d2,
 *   and pixelFormat in x2; it calls CGBitmapContextCreate at 0x22ff431a4.
 * - +[... derivePixelFormatFromCGImage:] at 0x22ff43278 has the same floating
 *   arguments and receives the source CGImage in x2.
 * - -[IFGraphicsContext image] at 0x22ff43570 calls CGBitmapContextCreateImage
 *   at 0x22ff43590, wraps that CGImage in a UIImage, releases the temporary
 *   CGImage, and returns the UIImage.  Its result must be sent -[UIImage CGImage]
 *   before any CGImage API is used.
 *
 * The Objective-C declarations below intentionally encode that ABI directly;
 * this target is compiled without iOS framework headers.
 */
#include <dlfcn.h>
#include <dispatch/dispatch.h>
#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef void *Obj;
typedef void *Sel;
typedef void *CGContextRef;
typedef void *CGImageRef;
typedef void *CGColorSpaceRef;
typedef void *CFStringRef;
typedef double CGFloat;
typedef struct { CGFloat width, height; } CGSize;
typedef struct { CGFloat x, y; } CGPoint;
typedef struct { CGPoint origin; CGSize size; } CGRect;

enum {
    kCFStringEncodingUTF8 = 0x08000100,
    kCGImageAlphaPremultipliedFirst = 2,
    kCGBitmapByteOrder32Little = 0x2000,
    kBGRA8PremultipliedFirst = kCGImageAlphaPremultipliedFirst |
                             kCGBitmapByteOrder32Little,
    kProbeWidth = 64,
    kProbeHeight = 64,
    kProbeBytesPerRow = kProbeWidth * 4,
};

static Sel (*sel_register)(const char *);
static Obj (*class_named)(const char *);
static void *msg_send;
static void *(*class_method)(Obj, Sel);
static void *(*instance_method)(Obj, Sel);
static void *(*pool_push)(void);
static void (*pool_pop)(void *);
static CGColorSpaceRef (*color_space_rgb)(void);
static CGContextRef (*bitmap_create)(void *, size_t, size_t, size_t, size_t,
                                     CGColorSpaceRef, uint32_t);
static CGImageRef (*bitmap_image)(CGContextRef);
static void (*context_release)(CGContextRef);
static void (*set_interpolation)(CGContextRef, int);
static void (*image_release)(CGImageRef);
static size_t (*image_width)(CGImageRef);
static size_t (*image_height)(CGImageRef);
static size_t (*image_bits_component)(CGImageRef);
static size_t (*image_bits_pixel)(CGImageRef);
static size_t (*image_bytes_row)(CGImageRef);
static uint32_t (*image_alpha)(CGImageRef);
static uint32_t (*image_bitmap_info)(CGImageRef);
static CGColorSpaceRef (*image_color_space)(CGImageRef);
static int (*color_space_model)(CGColorSpaceRef);
static size_t (*color_space_components)(CGColorSpaceRef);
static CFStringRef (*cf_string)(void *, const char *, uint32_t);
static void (*cf_release)(const void *);

static void stage(const char *name, const char *detail) {
    fprintf(stderr, "GRAPHICS_PROBE_STAGE %s %s\n", name, detail ? detail : "");
}
static void fatal(const char *kind, const char *name) {
    fprintf(stderr, "GRAPHICS_PROBE_ERROR %s=%s\n", kind, name);
    exit(1);
}
static void *framework(const char *path) {
    void *handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!handle) {
        fprintf(stderr, "GRAPHICS_PROBE_ERROR dlopen=%s error=%s\n", path, dlerror());
        exit(1);
    }
    fprintf(stderr, "GRAPHICS_PROBE_STAGE dlopen path=%s handle=%p\n", path, handle);
    return handle;
}
static void *required_symbol(void *handle, const char *name) {
    void *result = dlsym(handle, name);
    if (!result) fatal("missing-symbol", name);
    return result;
}
static Sel required_selector(Obj klass, const char *name, bool is_class_method) {
    Sel result = sel_register(name);
    if (!result || !(is_class_method ? class_method(klass, result) :
                                      instance_method(klass, result)))
        fatal("missing-class-selector", name);
    fprintf(stderr, "GRAPHICS_PROBE_STAGE selector name=%s kind=%s\n", name,
            is_class_method ? "class" : "instance");
    return result;
}
static Obj msg1(Obj object, Sel selector, Obj arg) {
    return ((Obj (*)(Obj, Sel, Obj))msg_send)(object, selector, arg);
}
static Obj msg0(Obj object, Sel selector) {
    return ((Obj (*)(Obj, Sel))msg_send)(object, selector);
}
static CGImageRef msg_cgimage(Obj object, Sel selector) {
    return ((CGImageRef (*)(Obj, Sel))msg_send)(object, selector);
}
static CGContextRef msg_cgcontext(Obj object, Sel selector) {
    return ((CGContextRef (*)(Obj, Sel))msg_send)(object, selector);
}
/* CGRect is an AArch64 HFA: the historical caller receives bounds in d0-d3
 * immediately before its drawCGImage:inRect: message. */
static CGRect msg_bounds(Obj object, Sel selector) {
    return ((CGRect (*)(Obj, Sel))msg_send)(object, selector);
}
static void msg_draw(Obj object, Sel selector, CGImageRef image, CGRect rect) {
    ((void (*)(Obj, Sel, CGImageRef, CGRect))msg_send)(object, selector, image, rect);
}
/* The prototype preserves AArch64's HFA CGSize and scalar-FP registers. */
static Obj msg_bitmap_pixel(Obj klass, Sel selector, CGSize size, CGFloat scale,
                            uint32_t pixel_format) {
    return ((Obj (*)(Obj, Sel, CGSize, CGFloat, uint32_t))msg_send)(
        klass, selector, size, scale, pixel_format);
}
static Obj msg_bitmap_derive(Obj klass, Sel selector, CGSize size, CGFloat scale,
                             CGImageRef image) {
    return ((Obj (*)(Obj, Sel, CGSize, CGFloat, CGImageRef))msg_send)(
        klass, selector, size, scale, image);
}
static uint32_t msg_pixel_format(Obj klass, Sel selector, CGImageRef image) {
    return ((uint32_t (*)(Obj, Sel, CGImageRef))msg_send)(klass, selector, image);
}

static void describe_image(const char *label, CGImageRef image) {
    if (!image) {
        fprintf(stderr, "GRAPHICS_PROBE_IMAGE label=%s image=NULL\n", label);
        return;
    }
    CGColorSpaceRef color_space = image_color_space(image);
    fprintf(stderr,
            "GRAPHICS_PROBE_IMAGE label=%s image=%p width=%zu height=%zu bpc=%zu "
            "bpp=%zu bpr=%zu alpha=0x%x bitmap=0x%x colorspace=%p model=%d components=%zu\n",
            label, image, image_width(image), image_height(image),
            image_bits_component(image), image_bits_pixel(image), image_bytes_row(image),
            image_alpha(image), image_bitmap_info(image), color_space,
            color_space ? color_space_model(color_space) : -1,
            color_space ? color_space_components(color_space) : 0);
}
struct probe_request {
    CGFloat width, height, scale;
    const char *png_path;
};
static bool parse_positive(const char *text, CGFloat *value) {
    char *end = NULL;
    errno = 0;
    double parsed = strtod(text, &end);
    if (errno || !end || *end || !(parsed > 0.0) || parsed > 4096.0) return false;
    *value = parsed;
    return true;
}
/* IconFoundation forwards scale unchanged to its bitmap factory.  In
 * particular, 0 is not normalized there: 0x22ff42ffc/43004 multiply the
 * logical extent by d2 and 0x22ff43194..431a4 pass those zero dimensions to
 * CGBitmapContextCreate.  Permit it only as a bounded diagnostic input. */
static bool parse_scale(const char *text, CGFloat *value) {
    char *end = NULL;
    errno = 0;
    double parsed = strtod(text, &end);
    if (errno || !end || end == text || *end || !(parsed >= 0.0) || parsed > 16.0) return false;
    *value = parsed;
    return true;
}
static bool parse_request(int argc, char **argv, struct probe_request *request) {
    *request = (struct probe_request){kProbeWidth, kProbeHeight, 1.0, NULL};
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--size")) {
            if (i + 2 >= argc || !parse_positive(argv[++i], &request->width) ||
                !parse_positive(argv[++i], &request->height)) return false;
        } else if (!strcmp(argv[i], "--scale")) {
            if (i + 1 >= argc || !parse_scale(argv[++i], &request->scale)) return false;
        } else if (!request->png_path) {
            request->png_path = argv[i];
        } else {
            return false;
        }
    }
    /* IFGraphicsContext rounds logical dimensions times scale before allocating.
     * Cap that exact physical extent and its area, but preserve the requested
     * logical values in the actual private-method call. */
    double physical_width = request->width * request->scale;
    double physical_height = request->height * request->scale;
    return physical_width <= 4096.0 && physical_height <= 4096.0 &&
           physical_width * physical_height <= 16.0 * 1024.0 * 1024.0;
}
static CGImageRef create_basic(void) {
    CGColorSpaceRef color_space = color_space_rgb();
    if (!color_space) {
        stage("basic-colorspace", "result=NULL");
        return NULL;
    }
    CGContextRef context = bitmap_create(NULL, kProbeWidth, kProbeHeight, 8,
                                         kProbeBytesPerRow, color_space,
                                         kBGRA8PremultipliedFirst);
    fprintf(stderr, "GRAPHICS_PROBE_CONTEXT label=basic-bgra8 context=%p width=%d height=%d bpr=%d bitmap=0x%x\n",
            context, kProbeWidth, kProbeHeight, kProbeBytesPerRow, kBGRA8PremultipliedFirst);
    CGImageRef image = context ? bitmap_image(context) : NULL;
    describe_image("basic-bgra8", image);
    if (context) context_release(context);
    /* CGColorSpace is returned retained by CreateDeviceRGB; CFRelease is valid. */
    cf_release(color_space);
    return image;
}
static void draw_source(const char *label, Obj context, CGImageRef source,
                        Sel cgcontext_selector, Sel bounds_selector, Sel draw_selector) {
    if (!context || !source) return;
    CGContextRef cgcontext = msg_cgcontext(context, cgcontext_selector);
    CGRect bounds = msg_bounds(context, bounds_selector);
    fprintf(stderr, "GRAPHICS_PROBE_DRAW label=%s context=%p cgcontext=%p bounds={%.17g,%.17g,%.17g,%.17g} source=%p\n",
            label, context, cgcontext, bounds.origin.x, bounds.origin.y,
            bounds.size.width, bounds.size.height, source);
    if (cgcontext) set_interpolation(cgcontext, 3);
    msg_draw(context, draw_selector, source, bounds);
    fprintf(stderr, "GRAPHICS_PROBE_DRAW label=%s submitted=1 interpolation=%d\n", label, 3);
}
static int probe_icon_context(const char *label, Obj klass, Sel pixel_selector,
                              Sel derive_selector, Sel image_selector,
                              Sel uiimage_cgimage_selector, Sel cgcontext_selector,
                              Sel bounds_selector, Sel draw_selector, CGImageRef source,
                              const struct probe_request *request) {
    if (!source) {
        fprintf(stderr, "GRAPHICS_PROBE_RESULT label=%s skipped=no-source-image\n", label);
        return 1;
    }
    CGSize size = {request->width, request->height};
    fprintf(stderr, "GRAPHICS_PROBE_STAGE request label=%s logical-width=%.17g logical-height=%.17g scale=%.17g\n",
            label, request->width, request->height, request->scale);
    uint32_t pixel_format = msg_pixel_format(klass, pixel_selector, source);
    fprintf(stderr, "GRAPHICS_PROBE_STAGE pixel-format label=%s source=%p value=0x%x\n",
            label, source, pixel_format);
    Obj pixel_context = msg_bitmap_pixel(klass,
        required_selector(klass, "bitmapContextWithSize:scale:pixelFormat:", true),
        size, request->scale, pixel_format);
    fprintf(stderr, "GRAPHICS_PROBE_CONTEXT label=%s-pixel context=%p pixelFormat=0x%x\n",
            label, pixel_context, pixel_format);
    draw_source("pixel", pixel_context, source, cgcontext_selector, bounds_selector, draw_selector);
    Obj pixel_uiimage = pixel_context ? msg0(pixel_context, image_selector) : NULL;
    CGImageRef pixel_image = pixel_uiimage ? msg_cgimage(pixel_uiimage, uiimage_cgimage_selector) : NULL;
    fprintf(stderr, "GRAPHICS_PROBE_UIIMAGE label=%s-pixel uiimage=%p cgimage=%p ownership=borrowed\n",
            label, pixel_uiimage, pixel_image);
    describe_image("if-pixel", pixel_image);

    Obj derived_context = msg_bitmap_derive(klass, derive_selector, size, request->scale, source);
    fprintf(stderr, "GRAPHICS_PROBE_CONTEXT label=%s-derived context=%p source=%p\n",
            label, derived_context, source);
    draw_source("derived", derived_context, source, cgcontext_selector, bounds_selector, draw_selector);
    Obj derived_uiimage = derived_context ? msg0(derived_context, image_selector) : NULL;
    CGImageRef derived_image = derived_uiimage ?
        msg_cgimage(derived_uiimage, uiimage_cgimage_selector) : NULL;
    fprintf(stderr, "GRAPHICS_PROBE_UIIMAGE label=%s-derived uiimage=%p cgimage=%p ownership=borrowed\n",
            label, derived_uiimage, derived_image);
    describe_image("if-derived", derived_image);
    /* Factory-method UIImages and IF contexts are autoreleased.  CGImage is a
     * borrowed UIImage accessor result, so neither is passed to CGImageRelease. */
    int failed = !pixel_context || !pixel_uiimage || !pixel_image ||
                 !derived_context || !derived_uiimage || !derived_image;
    fprintf(stderr, "GRAPHICS_PROBE_RESULT label=%s pixel-context=%d pixel-uiimage=%d pixel-cgimage=%d derived-context=%d derived-uiimage=%d derived-cgimage=%d\n",
            label, !!pixel_context, !!pixel_uiimage, !!pixel_image, !!derived_context,
            !!derived_uiimage, !!derived_image);
    return failed;
}
static CGImageRef png_image(const char *path, Obj uiimage_class, Sel load_selector,
                            Sel cgimage_selector) {
    CFStringRef string = cf_string(NULL, path, kCFStringEncodingUTF8);
    if (!string) {
        fprintf(stderr, "GRAPHICS_PROBE_ERROR png-path-cfstring=NULL path=%s\n", path);
        return NULL;
    }
    Obj ui_image = msg1(uiimage_class, load_selector, string);
    cf_release(string);
    if (!ui_image) {
        fprintf(stderr, "GRAPHICS_PROBE_ERROR png-load=NULL path=%s\n", path);
        return NULL;
    }
    CGImageRef image = msg_cgimage(ui_image, cgimage_selector);
    fprintf(stderr, "GRAPHICS_PROBE_STAGE png-load path=%s uiimage=%p cgimage=%p\n",
            path, ui_image, image);
    return image;
}
/* UIKit framework initialization can synchronously wait for work routed to the
 * main dispatch queue.  Keep that queue alive on the initial thread and run
 * the complete probe on one global-queue worker, matching the proven native
 * input service startup pattern. */
static struct probe_request worker_request;

static void run_probe(void *context) {
    const struct probe_request *request = context;
    stage("worker", "entered=1");
    stage("start", "protocol=1 allocation=64x64-bgra8");
    void *objc = framework("/usr/lib/libobjc.A.dylib");
    void *cf = framework("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation");
    void *cg = framework("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics");
    framework("/System/Library/Frameworks/UIKit.framework/UIKit");
    framework("/System/Library/PrivateFrameworks/IconFoundation.framework/IconFoundation");
    sel_register = required_symbol(objc, "sel_registerName");
    class_named = required_symbol(objc, "objc_getClass");
    msg_send = required_symbol(objc, "objc_msgSend");
    class_method = required_symbol(objc, "class_getClassMethod");
    instance_method = required_symbol(objc, "class_getInstanceMethod");
    pool_push = required_symbol(objc, "objc_autoreleasePoolPush");
    pool_pop = required_symbol(objc, "objc_autoreleasePoolPop");
    color_space_rgb = required_symbol(cg, "CGColorSpaceCreateDeviceRGB");
    bitmap_create = required_symbol(cg, "CGBitmapContextCreate");
    bitmap_image = required_symbol(cg, "CGBitmapContextCreateImage");
    context_release = required_symbol(cg, "CGContextRelease");
    set_interpolation = required_symbol(cg, "CGContextSetInterpolationQuality");
    image_release = required_symbol(cg, "CGImageRelease");
    image_width = required_symbol(cg, "CGImageGetWidth");
    image_height = required_symbol(cg, "CGImageGetHeight");
    image_bits_component = required_symbol(cg, "CGImageGetBitsPerComponent");
    image_bits_pixel = required_symbol(cg, "CGImageGetBitsPerPixel");
    image_bytes_row = required_symbol(cg, "CGImageGetBytesPerRow");
    image_alpha = required_symbol(cg, "CGImageGetAlphaInfo");
    image_bitmap_info = required_symbol(cg, "CGImageGetBitmapInfo");
    image_color_space = required_symbol(cg, "CGImageGetColorSpace");
    color_space_model = required_symbol(cg, "CGColorSpaceGetModel");
    color_space_components = required_symbol(cg, "CGColorSpaceGetNumberOfComponents");
    cf_string = required_symbol(cf, "CFStringCreateWithCString");
    cf_release = required_symbol(cf, "CFRelease");
    Obj if_context = class_named("IFGraphicsContext");
    Obj ui_image = class_named("UIImage");
    if (!if_context || !ui_image) fatal("missing-class", !if_context ? "IFGraphicsContext" : "UIImage");
    Sel pixel_selector = required_selector(if_context, "pixelFormatFromCGImage:", true);
    Sel derive_selector = required_selector(if_context,
        "bitmapContextWithSize:scale:derivePixelFormatFromCGImage:", true);
    Sel image_selector = required_selector(if_context, "image", false);
    Sel cgcontext_selector = required_selector(if_context, "cgContext", false);
    Sel bounds_selector = required_selector(if_context, "bounds", false);
    Sel draw_selector = required_selector(if_context, "drawCGImage:inRect:", false);
    Sel png_selector = required_selector(ui_image, "imageWithContentsOfFile:", true);
    Sel cgimage_selector = required_selector(ui_image, "CGImage", false);
    void *pool = pool_push();
    CGImageRef basic = create_basic();
    int failed = probe_icon_context("basic", if_context, pixel_selector, derive_selector,
                                   image_selector, cgimage_selector, cgcontext_selector,
                                   bounds_selector, draw_selector, basic, request);
    if (request->png_path) {
        CGImageRef png = png_image(request->png_path, ui_image, png_selector, cgimage_selector);
        describe_image("png-source", png);
        failed |= probe_icon_context("png", if_context, pixel_selector, derive_selector,
                                    image_selector, cgimage_selector, cgcontext_selector,
                                    bounds_selector, draw_selector, png, request);
    }
    if (basic) image_release(basic);
    pool_pop(pool);
    fprintf(stderr, "GRAPHICS_PROBE_COMPLETE failed-stages=%d\n", failed);
    exit(failed ? 3 : 0);
}

int main(int argc, char **argv) {
    /* Keep diagnostics visible when launchd supplies no usable stderr. */
    if (!freopen("/dev/console", "a", stderr)) return 2;
    setvbuf(stderr, NULL, _IONBF, 0);
    if (!parse_request(argc, argv, &worker_request)) {
        fprintf(stderr, "usage: %s [--size logical-width logical-height] [--scale scale] [existing.png]\n", argv[0]);
        fprintf(stderr, "GRAPHICS_PROBE_ERROR invalid-or-unbounded-request\n");
        return 2;
    }
    stage("dispatch", "worker=global main-loop=enter");
    dispatch_async_f(dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0),
                     &worker_request, run_probe);
    dispatch_main();
}
