#pragma once
// Fixed diagnostic output: native display geometry and pitch, in owned RAM.
#define DVM_PRESENT_OFFSET 0x300000u
#define DVM_PRESENT_WIDTH 1179u
#define DVM_PRESENT_HEIGHT 2556u
#define DVM_PRESENT_ROW 4864u
#define DVM_PRESENT_BYTES (DVM_PRESENT_ROW*DVM_PRESENT_HEIGHT)
#define DVM_PRESENT_BUFFER_BYTES ((DVM_PRESENT_BYTES+16383u)&~16383u)
// Mode-3 only: bounded final-only timing ledger, between reply and pixel RAM.
#define DVM_PRESENT_MAX_FRAMES 8192u
#define DVM_PRESENT_CONFIG 0x200u
#define DVM_PRESENT_METRICS 0x210000u
typedef struct {
    double gpu,draw,delivery,swap,total,target,start,finish;
    uint32_t swapID,frame,slot,reserved;
} DVMPresentSample;
_Static_assert(sizeof(DVMPresentSample)==80,"timing ABI");
_Static_assert(DVM_PRESENT_METRICS+16+DVM_PRESENT_MAX_FRAMES*80<DVM_PRESENT_OFFSET,"timing bounds");
