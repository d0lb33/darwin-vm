#pragma once
/* BLQ1/BLP1: bounded two-pass blur tile, raw snapshot, little endian.
 * Command state is preserved on wire and checked before resource mutation. */
#import <Foundation/Foundation.h>
#include "driver_binary.h"
#define DVM_BLUR_REQUEST 0x31514c42u
#define DVM_BLUR_REPLY 0x31504c42u
typedef struct {uint32_t magic,version;uint64_t seq;uint32_t length,w,h,bytes;uint64_t pipeline,input,tmp,output;} DVMBlurRequest;
typedef struct {uint8_t uniform[20],weights[10],reserved[2];uint32_t groups[3],threads[3],imageblock[2];} DVMBlurCommand;
typedef struct {uint32_t magic,version;uint64_t seq;uint32_t length,status;uint64_t gpu_ns,output;uint32_t w,h;uint8_t reserved[16];} DVMBlurReply;
_Static_assert(sizeof(DVMBlurRequest)==64 && sizeof(DVMBlurCommand)==64 && sizeof(DVMBlurReply)==64,"blur wire ABI");
static inline NSData *DVMBlurEncode(NSDictionary *r) {
 NSArray *cs=r[@"commands"],*us=r[@"uploads"];
 if(cs.count!=2||us.count!=1||[r[@"readbacks"] count]||[cs[0][@"textures"] count]!=2||[cs[1][@"textures"] count]!=2)return nil;
 NSData *data=DVMBPayload(us[0][@"data"]);unsigned w=[r[@"w"] unsignedIntValue],h=[r[@"h"] unsignedIntValue];
 if(!w||!h||w%32||h%32||w>256||h>256||data.length!=(w+32)*(h+32)*8)return nil;
 if(![cs[0][@"pipeline"] isEqual:cs[1][@"pipeline"]]||![cs[0][@"textures"][1] isEqual:cs[1][@"textures"][0]]||![us[0][@"texture"] isEqual:cs[0][@"textures"][0]]||[us[0][@"row"] unsignedIntValue]!=(w+32)*8)return nil;
 NSMutableData *frame=[NSMutableData dataWithLength:192+data.length];uint8_t *p=frame.mutableBytes;
 DVMBlurRequest head={DVM_BLUR_REQUEST,1,[r[@"seq"] unsignedLongLongValue],(uint32_t)frame.length,w,h,(uint32_t)data.length,[cs[0][@"pipeline"] unsignedLongLongValue],[cs[0][@"textures"][0] unsignedLongLongValue],[cs[0][@"textures"][1] unsignedLongLongValue],[cs[1][@"textures"][1] unsignedLongLongValue]};memcpy(p,&head,64);
 for(unsigned i=0;i<2;i++) {
  NSDictionary*c=cs[i];if([c[@"buffers"] count]||[c[@"threadgroupMemory"] count]||[c[@"bytes"] count]!=2||[c[@"groups"] count]!=3||[c[@"threads"] count]!=3||[c[@"imageblock"] count]!=2)return nil;
  DVMBlurCommand command={0};unsigned mask=0;
  for(NSDictionary*b in c[@"bytes"]){unsigned ix=[b[@"index"] unsignedIntValue];NSData*d=DVMBPayload(b[@"data"]);if(ix>1||(mask&(1u<<ix))||d.length!=(ix?10:20))return nil;mask|=1u<<ix;memcpy(ix?command.weights:command.uniform,d.bytes,d.length);}
  for(unsigned j=0;j<3;j++){command.groups[j]=[c[@"groups"][j] unsignedIntValue];command.threads[j]=[c[@"threads"][j] unsignedIntValue];}
  for(unsigned j=0;j<2;j++)command.imageblock[j]=[c[@"imageblock"][j] unsignedIntValue];memcpy(p+64+i*64,&command,64);
 }
 memcpy(p+192,data.bytes,data.length);return frame;
}
static inline NSDictionary *DVMBlurDecode(NSData*frame) {
 if(frame.length<192)return nil;DVMBlurRequest h;memcpy(&h,frame.bytes,64);
 if(h.magic!=DVM_BLUR_REQUEST||h.version!=1||!h.seq||h.length!=frame.length||!h.w||!h.h||h.w%32||h.h%32||h.w>256||h.h>256||h.bytes!=(h.w+32)*(h.h+32)*8||frame.length!=192+h.bytes)return nil;
 return @{@"op":@"blurSubmit",@"seq":@(h.seq),@"w":@(h.w),@"h":@(h.h),@"pipeline":@(h.pipeline),@"input":@(h.input),@"tmp":@(h.tmp),@"output":@(h.output),@"state":[frame subdataWithRange:NSMakeRange(64,128)],@"pixels":[frame subdataWithRange:NSMakeRange(192,h.bytes)]};
}
static inline NSData *DVMBlurReplyEncode(NSDictionary*r) {
 NSData*d=r[@"texture"];if(![d isKindOfClass:NSData.class])return nil;
 DVMBlurReply h={DVM_BLUR_REPLY,1,[r[@"seq"] unsignedLongLongValue],64+(uint32_t)d.length,4,[r[@"gpu_ns"] unsignedLongLongValue],[r[@"output"] unsignedLongLongValue],[r[@"w"] unsignedIntValue],[r[@"h"] unsignedIntValue],{0}};
 NSMutableData*f=[NSMutableData dataWithBytes:&h length:64];[f appendData:d];return f;
}
static inline NSDictionary *DVMBlurReplyDecode(NSData*f) {
 if(f.length<64)return nil;DVMBlurReply h;memcpy(&h,f.bytes,64);
 if(h.magic!=DVM_BLUR_REPLY||h.version!=1||!h.seq||h.status!=4||h.length!=f.length||!h.output||!h.w||!h.h||h.w%32||h.h%32||h.w>256||h.h>256||f.length!=64+h.w*h.h*8||!DVMBZero(h.reserved,16))return nil;
 return @{@"seq":@(h.seq),@"ok":@YES,@"status":@4,@"dispatches":@2,@"buffers":[NSDictionary dictionary],@"output":@(h.output),@"w":@(h.w),@"h":@(h.h),@"gpu_ns":@(h.gpu_ns),@"texture":[f subdataWithRange:NSMakeRange(64,f.length-64)]};
}
