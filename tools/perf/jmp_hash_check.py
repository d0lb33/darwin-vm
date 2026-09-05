#!/usr/bin/env python3
"""Compile the actual jump-cache hash functions and verify page invalidation.

Checks all byte offsets in 1/4/16/64KiB pages at 256 pseudo-random
addresses, plus aligned-instruction bucket coverage, at the actual cache size.
"""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(exist_ok=False)
    header = (ROOT / 'qemu-sptm/accel/tcg/tb-hash.h').read_text()
    funcs = re.search(r'static inline unsigned int tb_jmp_cache_hash_page.*?\n#else', header, re.S).group(0).removesuffix('\n#else')
    cache = (ROOT / 'qemu-sptm/accel/tcg/tb-jmp-cache.h').read_text()
    cache_bits = int(re.search(r'#define TB_JMP_CACHE_BITS (\d+)', cache)[1])
    macros = '\n'.join(line for line in header.splitlines() if line.startswith('#define TB_JMP_'))
    source = '''#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <assert.h>
typedef uint64_t vaddr;
static unsigned TARGET_PAGE_BITS;
#define TB_JMP_CACHE_BITS CACHE_BITS
#define TB_JMP_CACHE_SIZE (1 << TB_JMP_CACHE_BITS)
''' + macros + '\n' + funcs + '''
int main(void) {
    uint64_t checks = 0, ranges = 0, seed = 0x91f34d0ee632af17ULL;
    for (unsigned bits = 10; bits <= 16; bits += 2) {
        TARGET_PAGE_BITS = bits;
        {
            for (unsigned page = 0; page < 256; page++) {
                seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17;
                uint64_t base = (page == 0 ? 0 : page == 1 ? UINT64_MAX : seed)
                                & ~((1ULL << bits) - 1);
                unsigned page_hash = tb_jmp_cache_hash_page(base);
                for (unsigned offset = 0; offset < (1U << bits); offset++) {
                    unsigned hash = tb_jmp_cache_hash_func(base + offset);
                    assert(hash < TB_JMP_CACHE_SIZE);
                    assert(hash >= page_hash && hash < page_hash + TB_JMP_PAGE_SIZE);
                    checks++;
                }
                /* The new full-clear threshold must cover every page-hash
                 * group, even at each possible starting-page alignment. */
                enum { GROUPS = TB_JMP_CACHE_SIZE / TB_JMP_PAGE_SIZE };
                for (unsigned alignment = 0; alignment < GROUPS; alignment++) {
                    bool seen[GROUPS] = {0};
                    uint64_t start = base + ((uint64_t)alignment << bits);
                    for (unsigned p = 0; p < 2 * GROUPS - 1; p++) {
                        unsigned h = tb_jmp_cache_hash_page(start + ((uint64_t)p << bits));
                        seen[h / TB_JMP_PAGE_SIZE] = true;
                    }
                    for (unsigned g = 0; g < GROUPS; g++) assert(seen[g]);
                    ranges++;
                }
            }
        }
    }
    TARGET_PAGE_BITS = 12;
    unsigned buckets[TB_JMP_PAGE_SIZE] = {0}, used = 0;
    for (unsigned pc = 0; pc < 4096; pc += 4)
        buckets[tb_jmp_cache_hash_func(pc) & TB_JMP_ADDR_MASK]++;
    for (unsigned b = 0; b < TB_JMP_PAGE_SIZE; b++) used += buckets[b] != 0;
    assert(used == TB_JMP_PAGE_SIZE);
    printf("cache_bits=%u aligned_instruction_buckets=%u/%u\\n", TB_JMP_CACHE_BITS, used, TB_JMP_PAGE_SIZE);
    printf("PASS: %llu page-invalidation checks\\n", (unsigned long long)checks);
    printf("PASS: %llu complete range-coverage checks\\n", (unsigned long long)ranges);
}
'''
    source = source.replace('#define TB_JMP_CACHE_BITS CACHE_BITS', '#define TB_JMP_CACHE_BITS ' + str(cache_bits))
    c = a.out / 'check.c'; exe = a.out / 'check'
    c.write_text(source)
    subprocess.run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(exe)], check=True)
    result = subprocess.check_output([str(exe)], text=True)
    print(result, end='')
    (a.out / 'result.txt').write_text('header_sha256=' + hashlib.sha256(header.encode()).hexdigest() + '\n' + result)


if __name__ == '__main__':
    main()
