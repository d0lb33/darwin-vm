/* Experimental little-endian binary ABI for the already audited luma envelope.
 * Not a general command language. Persistent resource handles identify objects;
 * frame-relative slots carry snapshots, owned by the host until completion.
 * No guest pointers or arbitrary physical addresses cross this boundary. */
#import <Foundation/Foundation.h>
#include <stdint.h>
#include <string.h>
#if __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error Binary MVP requires a little-endian endpoint
#endif
#define DVM_BIN_REQUEST 0x31425644u /* DVB1 */
#define DVM_BIN_REPLY 0x31525644u   /* DVR1 */
#define DVM_BIN_BYTES 28672u

typedef struct {
    uint32_t magic,version;
    uint64_t seq;
    uint32_t length,commands,uploads,reads;
} DVMBHeader;
typedef struct {
    uint64_t pipeline,texture,buffer1,buffer2,offset1,offset2;
    uint32_t groups[3],threads[3],scratch;
    uint8_t uniform[20],reserved[32];
} DVMBCommand;
typedef struct {
    uint64_t handle;
    uint32_t kind,row,offset,length;
    uint64_t reserved;
} DVMBUpload;
typedef struct {
    uint32_t magic,version;
    uint64_t seq;
    uint32_t length,status,dispatches,reads;
    uint64_t gpu_us,handles[2];
    uint8_t reserved[72];
    uint8_t partial[96],padding[32],result[16];
} DVMBReply;
_Static_assert(sizeof(DVMBHeader)==32,"header ABI");
_Static_assert(sizeof(DVMBCommand)==128,"command ABI");
_Static_assert(sizeof(DVMBUpload)==32,"upload ABI");
_Static_assert(sizeof(DVMBReply)==272,"reply ABI");

static inline uint32_t DVMBMagic(NSData *d) {
    uint32_t m=0;if(d.length>=4)memcpy(&m,d.bytes,4);return m;
}
static inline BOOL DVMBZero(const void *p,size_t n) {
    const uint8_t *b=p;for(size_t i=0;i<n;i++)if(b[i])return NO;return YES;
}
static inline NSData *DVMBPayload(id value) {
    if([value isKindOfClass:NSData.class])return value;
    return [value isKindOfClass:NSString.class]?[[NSData alloc] initWithBase64EncodedString:value options:0]:nil;
}
static inline NSData *DVMBEncodeRequest(NSDictionary *r) {
    NSArray *commands=r[@"commands"],*uploads=r[@"uploads"],*reads=r[@"readbacks"];
    if(commands.count!=2||uploads.count!=3||reads.count!=2)return nil;
    NSMutableData *frame=[NSMutableData dataWithLength:DVM_BIN_BYTES];uint8_t *p=frame.mutableBytes;
    DVMBHeader h={DVM_BIN_REQUEST,1,[r[@"seq"] unsignedLongLongValue],DVM_BIN_BYTES,2,3,2};memcpy(p,&h,sizeof(h));
    for(unsigned i=0;i<2;i++) {
        NSDictionary *c=commands[i];NSArray *ts=c[@"textures"],*bs=c[@"buffers"],*us=c[@"bytes"],*scratch=c[@"threadgroupMemory"];
        if(ts.count!=(i?0:1)||bs.count!=(i?2:1)||us.count!=1||scratch.count!=1||[c[@"groups"] count]!=3||[c[@"threads"] count]!=3)return nil;
        NSData *uniform=DVMBPayload(us[0][@"data"]);
        if(uniform.length!=20||[us[0][@"index"] unsignedIntValue]||[scratch[0][@"index"] unsignedIntValue])return nil;
        DVMBCommand d={0};d.pipeline=[c[@"pipeline"] unsignedLongLongValue];d.texture=i?0:[ts[0] unsignedLongLongValue];
        for(NSDictionary *b in bs) {
            unsigned index=[b[@"index"] unsignedIntValue];
            if(index==1&&!d.buffer1){d.buffer1=[b[@"buffer"] unsignedLongLongValue];d.offset1=[b[@"offset"] unsignedLongLongValue];}
            else if(index==2&&!d.buffer2){d.buffer2=[b[@"buffer"] unsignedLongLongValue];d.offset2=[b[@"offset"] unsignedLongLongValue];}
            else return nil;
        }
        if(!d.pipeline||!d.buffer2||(!i&&(!d.texture||d.buffer1))||(i&&!d.buffer1))return nil;
        for(unsigned j=0;j<3;j++){d.groups[j]=[c[@"groups"][j] unsignedIntValue];d.threads[j]=[c[@"threads"][j] unsignedIntValue];}
        d.scratch=[scratch[0][@"length"] unsignedIntValue];memcpy(d.uniform,uniform.bytes,20);memcpy(p+32+i*128,&d,sizeof(d));
    }
    unsigned mask=0;
    for(unsigned i=0;i<3;i++) {
        NSDictionary *u=uploads[i];BOOL buffer=u[@"buffer"]!=nil;NSData *data=DVMBPayload(u[@"data"]);
        unsigned bit,off;
        if(buffer&&data.length==96){bit=1;off=512;}
        else if(buffer&&data.length==16){bit=2;off=640;}
        else if(!buffer&&data.length==24576){bit=4;off=4096;}
        else return nil;
        if((mask&bit)||(buffer&&u[@"texture"]))return nil;mask|=bit;
        DVMBUpload d={[u[buffer?@"buffer":@"texture"] unsignedLongLongValue],buffer?1:2,buffer?0:[u[@"row"] unsignedIntValue],off,(uint32_t)data.length,0};
        memcpy(p+288+i*32,&d,sizeof(d));memcpy(p+off,data.bytes,data.length);
    }
    for(unsigned i=0;i<2;i++){uint64_t handle=[reads[i] unsignedLongLongValue];memcpy(p+384+i*8,&handle,8);}
    return frame;
}
static inline NSDictionary *DVMBDecodeRequest(NSData *frame) {
    if(frame.length!=DVM_BIN_BYTES)return nil;
    const uint8_t *p=frame.bytes;DVMBHeader h;memcpy(&h,p,sizeof(h));
    if(h.magic!=DVM_BIN_REQUEST||h.version!=1||!h.seq||h.length!=frame.length||h.commands!=2||h.uploads!=3||h.reads!=2||!DVMBZero(p+400,112))return nil;
    NSMutableArray *commands=[NSMutableArray array],*uploads=[NSMutableArray array],*reads=[NSMutableArray array];
    for(unsigned i=0;i<2;i++) {
        DVMBCommand d;memcpy(&d,p+32+i*128,sizeof(d));
        if(!d.pipeline||!d.buffer2||!DVMBZero(d.reserved,32)||(!i&&(!d.texture||d.buffer1||d.offset1))||(i&&(!d.buffer1||d.texture)))return nil;
        NSMutableArray *bs=[NSMutableArray array];
        if(d.buffer1)[bs addObject:@{@"index":@1,@"buffer":@(d.buffer1),@"offset":@(d.offset1)}];
        [bs addObject:@{@"index":@2,@"buffer":@(d.buffer2),@"offset":@(d.offset2)}];
        [commands addObject:@{@"pipeline":@(d.pipeline),@"textures":i?@[]:@[@(d.texture)],@"buffers":bs,
          @"groups":@[@(d.groups[0]),@(d.groups[1]),@(d.groups[2])],@"threads":@[@(d.threads[0]),@(d.threads[1]),@(d.threads[2])],
          @"threadgroupMemory":@[@{@"index":@0,@"length":@(d.scratch)}],@"bytes":@[@{@"index":@0,@"data":[NSData dataWithBytes:d.uniform length:20]}]}];
    }
    unsigned mask=0;
    for(unsigned i=0;i<3;i++) {
        DVMBUpload d;memcpy(&d,p+288+i*32,sizeof(d));unsigned bit;
        if(d.kind==1&&d.length==96&&d.offset==512&&!d.row)bit=1;
        else if(d.kind==1&&d.length==16&&d.offset==640&&!d.row)bit=2;
        else if(d.kind==2&&d.length==24576&&d.offset==4096&&d.row==512)bit=4;
        else return nil;
        if(!d.handle||d.reserved||(mask&bit))return nil;mask|=bit;
        [uploads addObject:@{d.kind==1?@"buffer":@"texture":@(d.handle),@"row":@(d.row),@"data":[NSData dataWithBytes:p+d.offset length:d.length]}];
    }
    for(unsigned i=0;i<2;i++){uint64_t handle;memcpy(&handle,p+384+i*8,8);if(!handle)return nil;[reads addObject:@(handle)];}
    return @{@"seq":@(h.seq),@"op":@"submit",@"commands":commands,@"uploads":uploads,@"readbacks":reads,@"_binary":@YES};
}
static inline NSData *DVMBEncodeReply(NSDictionary *r) {
    if(![r[@"ok"] boolValue])return nil;
    NSDictionary *buffers=r[@"buffers"];if(buffers.count!=2)return nil;
    DVMBReply d={0};d.magic=DVM_BIN_REPLY;d.version=1;d.seq=[r[@"seq"] unsignedLongLongValue];d.length=sizeof(d);
    d.status=[r[@"status"] unsignedIntValue];d.dispatches=[r[@"dispatches"] unsignedIntValue];d.reads=2;d.gpu_us=[r[@"gpu_us"] unsignedLongLongValue];
    for(NSString *key in buffers) {
        NSData *data=DVMBPayload(buffers[key]);
        if(data.length==96&&!d.handles[0]){d.handles[0]=key.longLongValue;memcpy(d.partial,data.bytes,96);}
        else if(data.length==16&&!d.handles[1]){d.handles[1]=key.longLongValue;memcpy(d.result,data.bytes,16);}
        else return nil;
    }
    if(!d.handles[0]||!d.handles[1]||d.handles[0]==d.handles[1])return nil;
    return [NSData dataWithBytes:&d length:sizeof(d)];
}
static inline NSDictionary *DVMBDecodeReply(NSData *frame) {
    if(frame.length!=sizeof(DVMBReply))return nil;DVMBReply d;memcpy(&d,frame.bytes,sizeof(d));
    if(d.magic!=DVM_BIN_REPLY||d.version!=1||!d.seq||d.length!=sizeof(d)||d.status!=4||d.dispatches!=2||d.reads!=2||
       !d.handles[0]||!d.handles[1]||d.handles[0]==d.handles[1]||!DVMBZero(d.reserved,sizeof(d.reserved))||!DVMBZero(d.padding,sizeof(d.padding)))return nil;
    return @{@"seq":@(d.seq),@"ok":@YES,@"status":@(d.status),@"dispatches":@(d.dispatches),@"gpu_us":@(d.gpu_us),
      @"buffers":@{@(d.handles[0]).stringValue:[NSData dataWithBytes:d.partial length:96],@(d.handles[1]).stringValue:[NSData dataWithBytes:d.result length:16]}};
}
