#pragma once
// Fixed diagnostic output: native display geometry and pitch, in owned RAM.
#define DVM_PRESENT_OFFSET 0x300000u
#define DVM_PRESENT_WIDTH 1179u
#define DVM_PRESENT_HEIGHT 2556u
#define DVM_PRESENT_ROW 4864u
#define DVM_PRESENT_BYTES (DVM_PRESENT_ROW*DVM_PRESENT_HEIGHT)
#define DVM_PRESENT_BUFFER_BYTES ((DVM_PRESENT_BYTES+16383u)&~16383u)
