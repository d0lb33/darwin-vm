/* Compile against the actual candidate header; no QEMU runtime needed. */
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <assert.h>
#include "tlb-page-payload.h"
int main(void)
{
    uint64_t x = 0x73194ab56cdef012ULL, payload;
    for (unsigned i = 0; i < 1000000; i++) {
        x ^= x << 13; x ^= x >> 7; x ^= x << 17;
        uint64_t addr = x & 0x00fffffffffff000ULL;
        if (addr & (1ULL << 55)) addr |= 0xff00000000000000ULL;
        uint32_t idx = i & 65535;
        assert(tlb_page_payload_pack(addr, idx, &payload));
        assert(tlb_page_payload_addr(payload) == addr);
        assert(tlb_page_payload_idx(payload) == idx);
        assert(!tlb_page_payload_pack(addr, idx | 65536, &payload));
        assert(!tlb_page_payload_pack(addr | 1, idx, &payload));
        assert(!tlb_page_payload_pack(addr ^ (1ULL << 63), idx, &payload));
    }
    puts("PASS: 1000000 actual-header round trips and rejected inputs");
}
