#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
int main(void){@autoreleasepool{
    MTLComputePipelineDescriptor *d=[MTLComputePipelineDescriptor new];
    MTLStageInputOutputDescriptor *s=d.stageInputDescriptor;
    NSMutableArray *attributes=[NSMutableArray array],*layouts=[NSMutableArray array],*mutability=[NSMutableArray array];
    for(unsigned i=0;i<31;i++){
        [attributes addObject:@[@(s.attributes[i].format),@(s.attributes[i].offset),@(s.attributes[i].bufferIndex)]];
        [layouts addObject:@[@(s.layouts[i].stride),@(s.layouts[i].stepFunction),@(s.layouts[i].stepRate)]];
        [mutability addObject:@(d.buffers[i].mutability)];
    }
    NSDictionary *result=@{@"scope":@"native host default compute descriptor, not exact guest",@"stageInputPresent":@(s!=nil),@"indexType":@(s.indexType),@"indexBufferIndex":@(s.indexBufferIndex),@"attributes":attributes,@"layouts":layouts,@"mutability":mutability,@"linkedPresent":@(d.linkedFunctions!=nil),@"linkedFunctionCount":@(d.linkedFunctions.functions.count),@"linkedBinaryCount":@(d.linkedFunctions.binaryFunctions.count),@"linkedPrivateCount":@(d.linkedFunctions.privateFunctions.count),@"linkedGroups":@(d.linkedFunctions.groups.count),@"stack":@(d.maxCallStackDepth)};
    NSData *json=[NSJSONSerialization dataWithJSONObject:result options:NSJSONWritingPrettyPrinted error:nil];fwrite(json.bytes,1,json.length,stdout);puts("");
}}
