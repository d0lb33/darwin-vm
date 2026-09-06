// Same public Metal API workload for a native host device or our guest driver.
// AIR metadata: average offset 941671, sum offset 946967 in the exact slice.
#import "driver_api.h"
#include <string.h>
#include <time.h>

static uint64_t micros(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000 + t.tv_nsec / 1000;
}
static void require(BOOL b, const char *s) {
    if (!b) {
        fprintf(stderr, "GPU_LOAD_ERROR driver=%s\n", s);
        exit(1);
    }
}
int DVMRunLuma(id<MTLDevice> device, NSData *air, unsigned runs) {
    require(device != nil, "device");
    NSError *error = nil;
    // Keep the NSData alive through the destructor block instead of asking
    // dispatch_data_create to allocate and copy another complete AIR image.
    fprintf(stderr, "GPU_LOAD_DRIVER_STAGE air-dispatch-data\n");
    dispatch_data_t data = dispatch_data_create(air.bytes, air.length, NULL, ^{
        (void)air.length;
    });
    fprintf(stderr, "GPU_LOAD_DRIVER_STAGE library-request\n");
    id<MTLLibrary> library = [device newLibraryWithData:data error:&error];
    require(library != nil, "library");
    id<MTLComputePipelineState> average = [device
        newComputePipelineStateWithFunction:[library newFunctionWithName:@"compute_average_luma"]
                                      error:&error];
    id<MTLComputePipelineState> sum = [device
        newComputePipelineStateWithFunction:[library newFunctionWithName:@"compute_sum_luma"]
                                      error:&error];
    require(average && sum, "pipelines");
    MTLTextureDescriptor *desc =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float
                                                           width:64
                                                          height:48
                                                       mipmapped:NO];
    desc.storageMode = MTLStorageModeShared;
    desc.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> input = [device newTextureWithDescriptor:desc];
    id<MTLBuffer> partial = [device newBufferWithLength:96 options:MTLResourceStorageModeShared];
    id<MTLBuffer> result = [device newBufferWithLength:16 options:MTLResourceStorageModeShared];
    id<MTLCommandQueue> queue = [device newCommandQueue];
    require(input && partial && result && queue, "resources");
    require(input.device == device && partial.device == device && queue.device == device,
            "device-identity");
    const uint8_t uniform[20] = {0, 0, 0, 0, 64, 0, 48, 0, 1, 0, 0, 0, 6, 0, 0, 0, 1, 0, 0, 0};
    for (unsigned run = 0; run < runs; run++) {
        @autoreleasepool {
            uint32_t nonce = arc4random();
            uint64_t cpuStart = micros();
            _Float16 pixels[64 * 48 * 4];
            float expected[4] = {0}, groups[6][4] = {0};
            for (unsigned y = 0; y < 48; y++)
                for (unsigned x = 0; x < 64; x++)
                    for (unsigned c = 0; c < 4; c++) {
                        unsigned v = (x * 3 + y * 5 + c * 7 + (nonce >> ((c % 4) * 8))) & 15;
                        float value = (float)v / 16 + (float)((nonce >> (c * 4)) & 7);
                        pixels[(y * 64 + x) * 4 + c] = (_Float16)value;
                        expected[c] += value;
                        groups[y / 8][c] += value;
                    }
            // Conservative CPU comparison: this also includes input generation
            // and half conversion, beyond the reduction performed by the GPU.
            uint64_t cpuReference = micros() - cpuStart;
            uint64_t workStart = micros();
            [input replaceRegion:MTLRegionMake2D(0, 0, 64, 48)
                     mipmapLevel:0
                       withBytes:pixels
                     bytesPerRow:64 * 8];
            memset(partial.contents, 0xa5, 96);
            memset(result.contents, 0xa5, 16);
            id<MTLCommandBuffer> cb = [queue commandBuffer];
            id<MTLComputeCommandEncoder> e = [cb computeCommandEncoder];
            [e setComputePipelineState:average];
            [e setTexture:input atIndex:0];
            [e setBytes:uniform length:20 atIndex:0];
            [e setBuffer:partial offset:0 atIndex:2];
            [e setThreadgroupMemoryLength:1024 atIndex:0];
            [e dispatchThreadgroups:MTLSizeMake(1, 6, 1)
                threadsPerThreadgroup:MTLSizeMake(8, 8, 1)];
            [e endEncoding];
            e = [cb computeCommandEncoder];
            [e setComputePipelineState:sum];
            [e setBytes:uniform length:20 atIndex:0];
            [e setBuffer:partial offset:0 atIndex:1];
            [e setBuffer:result offset:0 atIndex:2];
            [e setThreadgroupMemoryLength:128 atIndex:0];
            [e dispatchThreadgroups:MTLSizeMake(1, 1, 1)
                threadsPerThreadgroup:MTLSizeMake(8, 1, 1)];
            [e endEncoding];
            dispatch_semaphore_t signal = dispatch_semaphore_create(0);
            __block BOOL handlerOK = NO;
            [cb addCompletedHandler:^(id<MTLCommandBuffer> b) {
                handlerOK = b.status == MTLCommandBufferStatusCompleted;
                dispatch_semaphore_signal(signal);
            }];
            uint64_t start = micros();
            [cb commit];
            uint64_t committed = micros();
            // Waiting on our handler proves completion progresses without a call
            // to waitUntilCompleted or status polling from the caller.
            require(dispatch_semaphore_wait(
                        signal, dispatch_time(DISPATCH_TIME_NOW, 30 * NSEC_PER_SEC)) == 0,
                    "completion-handler-deadline");
            [cb waitUntilCompleted];
            uint64_t finished = micros();
            require(handlerOK && cb.status == MTLCommandBufferStatusCompleted && !cb.error,
                    "command-completion");
            require(!memcmp(result.contents, expected, 16), "sum-oracle");
            require(!memcmp(partial.contents, groups, 96), "partial-oracle");
            uint32_t bits[4];
            memcpy(bits, result.contents, 16);
            fprintf(
                stderr,
                "GPU_LOAD_DRIVER_RUN run=%u nonce=%u verified=1 dispatches=2 commit_us=%llu "
                "completed_us=%llu work_us=%llu cpu_reference_us=%llu result=%08x,%08x,%08x,%08x\n",
                run + 1, nonce, (unsigned long long)(committed - start),
                (unsigned long long)(finished - start), (unsigned long long)(finished - workStart),
                (unsigned long long)cpuReference, bits[0], bits[1], bits[2], bits[3]);
        }
    }
    return 0;
}
