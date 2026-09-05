#!/usr/bin/env python3
"""Partition macOS sample trees by exclusive vCPU stack weights.

Samples include blocked threads; these are wall-time observations, NOT CPU
utilization percentages. Unknown leaf addresses under cpu_tb_exec are reported
as JIT/unresolved (not native guest-instruction cost). Ancestors only identify
which operation owns a sample; weights are never counted twice.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re


def classify(stack):
    joined = ' '.join(stack)
    if 'qemu_process_cpu_events' in joined or 'qemu_wait_io_event' in joined:
        return 'cpu_event_wait_or_work'
    if 'cpu_exec_start' in joined or 'start_exclusive' in joined:
        return 'cpu_exclusive_coordination'
    if any(x in joined for x in ('ans_io', 'ans_submit', 'blk_pread', 'blk_pwrite')):
        return 'storage_service'
    if any(x in joined for x in ('__psynch', '_pthread_cond_wait', '_pthread_mutex', 'qemu_mutex_')):
        if 'helper_get_cp_reg' in joined:
            return 'sysreg_lock'
        return 'other_lock_or_wait'
    if any(x in joined for x in ('probe_access', 'mmu_lookup', 'tlb_', 'get_phys_addr', 'S1_ptw', 'get_page_addr_code', 'get_S1prot')):
        return 'memory_translation_or_tlb'
    if any(x in joined for x in ('pauth_', 'helper_aut', 'helper_paci', 'helper_pacd', 'helper_xpaci')):
        return 'pointer_authentication'
    if any(x in joined for x in ('tb_gen_code', 'tcg_gen_code', 'translator_loop', 'tb_flush')):
        return 'code_translation'
    if any(x in joined for x in ('lookup_tb_ptr', 'tb_lookup', 'tb_htable', 'qht_lookup', 'arm_get_tb_cpu_state')):
        return 'block_dispatch'
    if any(x in joined for x in ('helper_get_cp_reg', 'gt_virt_cnt_read', 'gt_cnt_read')):
        return 'sysreg_other'
    if stack[-1].startswith('???') and 'cpu_tb_exec' in joined:
        return 'jit_or_unresolved'
    return 'other'


def report(path):
    nodes = []
    stack = []
    totals = Counter()
    for line in path.read_text().splitlines():
        if line.startswith('Total number in stack'):
            break
        m = re.match(r'([ +!:|]*)(\d+) (.+)', line)
        if not m:
            continue
        depth, count, name = len(m[1]), int(m[2]), m[3]
        if name.startswith('Thread_'):
            stack = []
            cpu = re.search(r'CPU (\d+)/TCG', name)
            current = cpu[1] if cpu else None
            if current is not None:
                totals[current] += count
            continue
        if not stack and 'current' not in locals():
            continue
        if current is None:
            continue
        while stack and stack[-1]['depth'] >= depth:
            stack.pop()
        node = {'depth': depth, 'self': count, 'cpu': current, 'name': name,
                'path': [n['name'] for n in stack] + [name]}
        if stack:
            stack[-1]['self'] -= count
        nodes.append(node)
        stack.append(node)
    categories = {cpu: Counter() for cpu in totals}
    leaves = Counter()
    for node in nodes:
        assert node['self'] >= 0, node
        if node['self']:
            categories[node['cpu']][classify(node['path'])] += node['self']
            leaves[node['name'].split('  (in ', 1)[0]] += node['self']
    for cpu, total in totals.items():
        assert sum(categories[cpu].values()) == total, (cpu, categories[cpu], total)
    aggregate = sum(categories.values(), Counter())
    return {'source': str(path), 'thread_samples': dict(totals),
            'categories_by_cpu': categories, 'categories_all_cpus': aggregate,
            'top_exclusive_leaves': leaves.most_common(25),
            'denominator': 'wall-time samples of all six vCPU threads, includes waits'}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('samples', nargs='+', type=Path)
    a = ap.parse_args()
    print(json.dumps([report(p) for p in a.samples], indent=2))
