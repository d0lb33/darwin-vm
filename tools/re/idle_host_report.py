#!/usr/bin/env python3
"""Categorise a macOS `sample` call tree of QEMU by what TCG was doing.

Extends smp_storage_report.sample_tree's categories with the floating-point,
vector, system-register and TLB-maintenance helpers that dominate an idle
lock/home-screen guest (2026-09-06, docs/re/tcg-idle-profile.md).  Each
observation is attributed once, by the deepest matching ancestor set, so the
percentages add up to 100% of the vCPU thread's observations, waits included.

    tools/re/idle_host_report.py /tmp/dvm/idleprof/<TAG>/host-sample.txt
"""
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smp_storage_report  # noqa: E402


def classify(path):
    names = " ".join(path)
    if "__psynch_cvwait" in names:
        return "host_condition_wait"
    if "__psynch_mutexwait" in names:
        return "host_mutex_wait"
    if "ans_io" in names:
        return "storage_command"
    if re.search(r"float16|f16_to|to_float16|fcvt_f16|fcvt_f32_to_f16|bfloat16", names):
        return "fp16_conversion_softfloat"
    if re.search(r"helper_(?:gvec|neon|sve|advsimd)", names) or re.search(r"helper_vfp_|float32_|float64_|soft_f32|soft_f64|parts64|parts128|float_raise|round_to_int", names):
        return "fp_and_simd_helpers_softfloat"
    if "pauth_" in names or re.search(r"helper_(?:pac|aut|xpac)", names):
        return "pointer_authentication"
    if re.search(r"tlb_flush|tlb_reset_dirty|tlb_set_dirty", names):
        return "tlb_maintenance"
    if "get_phys_addr" in names or "tlb_fill" in names or "S1_ptw" in names or "tlb_set_page" in names:
        return "mmu_translation"
    if re.search(r"probe_access|mmu_lookup|helper_(?:ld|st)|do_ld|do_st|cpu_ld|cpu_st|io_read|io_write|memory_region_dispatch", names):
        return "memory_access_slow_path"
    if "helper_lookup_tb_ptr" in names or "tb_htable_lookup" in names or "qht_lookup" in names:
        return "translated_block_lookup"
    if "tb_gen_code" in names or "translator_loop" in names or "tcg_gen_code" in names:
        return "code_generation"
    if re.search(r"helper_(?:access_check_cp_reg|get_cp_reg|set_cp_reg|msr|mrs)|arm_hcr_el2_eff|arm_sctlr|arm_mmu_idx|arm_get_tb_cpu_state|rebuild_hflags", names):
        return "system_register_and_hflags"
    if re.search(r"helper_(?:wfi|wfe|yield|exception)|arm_cpu_do_interrupt|cpu_handle_interrupt|do_raise_exception", names):
        return "exceptions_and_interrupts"
    if "cpu_tb_exec" in names:
        return "translated_code_and_other_helpers"
    return "other_cpu_management"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    smp_storage_report.classify = classify
    result = smp_storage_report.sample_tree(Path(sys.argv[1]))
    total = Counter()
    for cpu, row in sorted(result.items(), key=lambda kv: int(kv[0])):
        obs = row["observations"]
        cats = Counter(row["categories"])
        total.update(cats)
        print("CPU %s: %d observations" % (cpu, obs))
        for name, n in cats.most_common():
            print("   %6.2f%%  %s" % (100 * n / obs, name))
    obs = sum(total.values())
    print("ALL vCPUs: %d observations" % obs)
    for name, n in total.most_common():
        print("   %6.2f%%  %s" % (100 * n / obs, name))


if __name__ == "__main__":
    main()
