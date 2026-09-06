#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <time.h>
#include "blur_host.h"
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec*1e-9;}
int main(int argc,char **argv){@autoreleasepool{
 if(argc!=5)return 2;unsigned w=atoi(argv[2]),h=atoi(argv[3]),runs=atoi(argv[4]);
 if(!w||!h||w%32||h%32||w>1184||h>2560||!runs||runs>200)return 2;
 double setup=now();id<MTLDevice>d=MTLCreateSystemDefaultDevice();NSError *err=nil;
 id<MTLLibrary>lib=[d newLibraryWithURL:[NSURL fileURLWithPath:@(argv[1])] error:&err];if(!lib){fprintf(stderr,"%s\n",err.description.UTF8String);return 3;}
 DVMBlurHost*b=[[DVMBlurHost alloc]initWithDevice:d library:lib width:w height:h];if(!b)return 4;
 size_t ni=(size_t)(w+32)*(h+32)*8,nt=(size_t)w*(h+32)*8,no=(size_t)w*h*8;
 DVMHalf *in=malloc(ni),*tmp=malloc(nt),*out=malloc(no),*ref=malloc(no);
 if(!in||!tmp||!out||!ref)return 5;
 fprintf(stderr,"SETUP device=%s us=%.3f imageblock=%lu\n",d.name.UTF8String,(now()-setup)*1e6,(unsigned long)[b.pipeline imageblockMemoryLengthForDimensions:MTLSizeMake(32,32,1)]);
 for(unsigned mode=0;mode<3;mode++){
 double steadyStart=0;
 for(unsigned run=0;run<runs;run++){@autoreleasepool{
  // Modes 1/2 upload once. Mode 2 validates after the batch, not per frame.
  if(run==1)steadyStart=now();
  if(!mode||!run){DVMBlurInput(in,w+32,h+32,run+1234);DVMBlurCPU(in,tmp,ref,w,h);}
  double start=now();if(!mode||!run)[b.input replaceRegion:MTLRegionMake2D(0,0,w+32,h+32) mipmapLevel:0 withBytes:in bytesPerRow:(w+32)*8];
  double uploaded=now();id<MTLCommandBuffer>cb=[b encode];[cb commit];[cb waitUntilCompleted];double completed=now();
  if(cb.status!=MTLCommandBufferStatusCompleted){fprintf(stderr,"%s\n",cb.error.description.UTF8String);return 6;}
  if(mode==2 && run+1==runs && runs>1)fprintf(stderr,"RESIDENT_BATCH width=%u height=%u frames=%u steady_wall_us=%.3f\n",w,h,runs-1,(completed-steadyStart)*1e6);
  BOOL verify=mode<2||run+1==runs;
  if(verify)[b.output getBytes:out bytesPerRow:w*8 fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];double delivered=now();
  unsigned bad=0;float maxerr=0;for(size_t i=0;verify&&i<no/2;i++){float e=fabsf((float)out[i]-(float)ref[i]);if(e>maxerr)maxerr=e;if(memcmp(out+i,ref+i,2)){if(bad<4)fprintf(stderr,"mismatch i=%zu got=%g expected=%g\n",i,(double)out[i],(double)ref[i]);bad++;}}
  printf("{\"width\":%u,\"height\":%u,\"mode\":\"%s\",\"run\":%u,\"upload_us\":%.3f,\"submit_complete_us\":%.3f,\"readback_us\":%.3f,\"full_us\":%.3f,\"gpu_us\":%.3f,\"verified\":%s,\"validation_readback_us\":%.3f,\"bad\":%u,\"max_error\":%.9g}\n",w,h,mode==2?"resident-no-readback":mode?"resident":"upload-every-frame",run,(uploaded-start)*1e6,(completed-uploaded)*1e6,mode==2?0:(delivered-completed)*1e6,((mode==2?completed:delivered)-start)*1e6,(cb.GPUEndTime-cb.GPUStartTime)*1e6,verify?"true":"false",mode==2&&verify?(delivered-completed)*1e6:0,bad,maxerr);fflush(stdout);if(bad)return 7;
 }}}
 free(in);free(tmp);free(out);free(ref);return 0;
}}
