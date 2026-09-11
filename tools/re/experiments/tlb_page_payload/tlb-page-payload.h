/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef TCG_TLB_PAGE_PAYLOAD_H
#define TCG_TLB_PAGE_PAYLOAD_H

/* A canonical 56-bit, 256-byte-aligned address leaves two bytes available.
 * Keep all 16 supported mask bits; callers fall back for larger masks.
 * This encodes work parameters only, never an emulated translation entry.
 */
static inline uint64_t tlb_page_payload_addr(uint64_t payload)
{
    uint64_t addr = payload & UINT64_C(0x00ffffffffffff00);
    return addr | ((UINT64_C(0) - ((addr >> 55) & 1)) << 56);
}

static inline uint32_t tlb_page_payload_idx(uint64_t payload)
{
    return (payload >> 56) | ((payload & 255) << 8);
}

static inline bool tlb_page_payload_pack(uint64_t addr, uint32_t idx,
                                         uint64_t *payload)
{
    if ((addr & 255) || idx > UINT16_MAX ||
        tlb_page_payload_addr(addr) != addr) {
        return false;
    }
    *payload = (addr & UINT64_C(0x00ffffffffffff00)) |
               ((uint64_t)(idx & 255) << 56) | (idx >> 8);
    return true;
}
#endif
