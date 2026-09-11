#!/usr/bin/env python3
"""Compile actual old/new SHA256 helper bodies and differential-test on ARM64."""
import argparse
from pathlib import Path
import re
import subprocess


def function(text, name):
    match = re.search(r'^(?:static (?:inline )?)?(?:void|uint32_t|intptr_t) ' + re.escape(name) + r'\([^;]*?\)\s*\{', text, re.M)
    if not match:
        raise ValueError(name)
    start = match.start()
    i = match.end()
    depth = 1
    while depth:
        depth += (text[i] == '{') - (text[i] == '}')
        i += 1
    return text[start:i]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('out', type=Path)
    a = p.parse_args()
    root = Path(__file__).resolve().parents[2] / 'qemu-sptm'
    rel = 'target/arm/tcg/crypto_helper.c'
    old = subprocess.check_output(['git', '-C', str(root), 'show', 'HEAD:' + rel], text=True)
    new = (root / rel).read_text()
    if 'DVM_NATIVE_SHA256' not in new or 'vsha256hq_u32' not in new:
        raise SystemExit('apply the native SHA256 candidate patch before testing')
    vec = (root / 'target/arm/tcg/vec_internal.h').read_text()
    desc = (root / 'include/tcg/tcg-gvec-desc.h').read_text()
    a.out.mkdir(parents=True, exist_ok=True)
    pre = '''#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <assert.h>
#include <arm_neon.h>
#define SIMD_MAXSZ_SHIFT 0
#define SIMD_MAXSZ_BITS 8
#define SIMD_OPRSZ_SHIFT 8
#define SIMD_OPRSZ_BITS 2
static uint32_t extract32(uint32_t x,int s,int n){return (x>>s)&((1u<<n)-1);}
static uint32_t ror32(uint32_t x,int n){return (x>>n)|(x<<(32-n));}
union CRYPTO_STATE {uint8_t bytes[16];uint32_t words[4];uint64_t l[2];};
#define CR_ST_WORD(s,i) ((s).words[i])
'''
    common = '\n'.join(function(desc, n) for n in ['simd_maxsz','simd_oprsz']) + '\n' + function(vec, 'clear_tail')
    ops = ['h','h2','su0','su1']
    for label, text in [('old', old), ('new', new)]:
        body = pre + f'\n#define HELPER(x) {label}_##x\n' + common + '\n'
        body += '\n'.join(function(text, n) for n in ['clear_tail_16','cho','maj','S0','S1','s0','s1'])
        body += '\n#define DVM_NATIVE_SHA256 ' + str(int(label == 'new')) + '\n'
        body += '\n'.join(function(text, 'HELPER(crypto_sha256'+op+')') for op in ops)
        (a.out / (label+'.c')).write_text(body)
    harness = '''#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <assert.h>
'''
    for label in ['old','new']:
        for op in ops:
            harness += f'void {label}_crypto_sha256{op}(void*,void*,' + ('' if op=='su0' else 'void*,') + 'uint32_t);\n'
    harness += '''int main(void) {
uint64_t seed=0x6a09e667bb67ae85ULL;
for(unsigned k=0;k<100000;k++) {
 uint64_t initial[3][32], a[3][32], b[3][32];
 for(unsigned i=0;i<96;i++){seed^=seed<<13;seed^=seed>>7;seed^=seed<<17;((uint64_t*)initial)[i]=seed;}
 for(unsigned alias=0;alias<5;alias++) {
 unsigned n=(alias==1||alias==4)?0:1, m=(alias==2||alias==4)?0:(alias==3?n:2);
 unsigned sizes[]={16,32,64,128,256}, size=sizes[k%5];
 uint32_t desc=(size/8-1)|(1<<8);
'''
    for op in ops:
        harness += 'memcpy(a,initial,sizeof(a));memcpy(b,initial,sizeof(b));\n'
        args = '[0],a[m]' if op=='su0' else '[0],a[n],a[m]'
        harness += f'old_crypto_sha256{op}(a{args},desc);\n'
        harness += f'new_crypto_sha256{op}(b{args.replace("a[","b[")},desc);\n'
        harness += 'assert(memcmp(a,b,sizeof(a))==0);for(unsigned j=16;j<size;j++)assert(((uint8_t*)b[0])[j]==0);\n'
    harness += '}} puts("PASS: 2000000 differential helper calls, aliases and tails");}\n'
    (a.out/'test.c').write_text(harness)
    binary=a.out/'test'
    subprocess.run(['clang','-O2','-fsanitize=address,undefined',str(a.out/'old.c'),str(a.out/'new.c'),str(a.out/'test.c'),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)


if __name__ == '__main__':
    main()
