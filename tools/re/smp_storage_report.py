#!/usr/bin/env python3
"""Summarize optional ANS wall timings and macOS sample call trees.

ANS times include host scheduling, block I/O, allocation and emulated DMA.
Sample percentages are thread observations, including waits, not CPU-time
percentages. Each observation is assigned once, using its complete call path.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re


def classify(path):
    names = " ".join(path)
    if "ans_io" in names:
        return "storage_command"
    if "__psynch_mutexwait" in names:
        return "host_mutex_wait"
    if "__psynch_cvwait" in names:
        return "host_condition_wait"
    if "pauth_" in names or re.search(r"helper_(?:pac|aut|xpac)", names):
        return "pointer_authentication"
    if "get_phys_addr" in names or "tlb_fill" in names:
        return "mmu_translation"
    if "helper_lookup_tb_ptr" in names or "tb_htable_lookup" in names:
        return "translated_block_lookup"
    if "tb_gen_code" in names:
        return "code_generation"
    if "cpu_tb_exec" in names:
        return "other_guest_execution_and_helpers"
    return "other_cpu_management"


def sample_tree(path):
    roots = []
    stack = []
    in_graph = False
    for line in path.read_text().splitlines():
        if line == "Call graph:":
            in_graph = True
            continue
        if line.startswith("Total number in stack"):
            break
        if not in_graph:
            continue
        m = re.match(r"^([ +!|:]*)(\d+) (.*)", line)
        if not m:
            continue
        depth, count, name = len(m[1]), int(m[2]), m[3]
        node = {"count": count, "name": name, "children": [], "depth": depth}
        while stack and stack[-1]["depth"] >= depth:
            stack.pop()
        if stack:
            stack[-1]["children"].append(node)
        else:
            roots.append(node)
        stack.append(node)

    def walk(node, ancestors, counts):
        names = ancestors + [node["name"]]
        own = node["count"] - sum(c["count"] for c in node["children"])
        assert own >= 0, node["name"]
        counts[classify(names)] += own
        for child in node["children"]:
            walk(child, names, counts)

    result = {}
    for root in roots:
        m = re.search(r"CPU (\d+)/TCG", root["name"])
        if m:
            counts = Counter()
            walk(root, [], counts)
            assert sum(counts.values()) == root["count"]
            result[m[1]] = {"observations": root["count"],
                            "categories": dict(counts)}
    assert result, f"no CPU call trees in {path}"
    return result


def storage_window(first, last):
    delta = {k: last[k] - first.get(k, 0) for k in last if not k.endswith("_max_ns")}
    seconds = delta["elapsed_ns"] / 1e9
    total = sum(delta[f"{kind}_ns"] for kind in ("read", "write", "flush", "other")) / 1e9
    return {"elapsed_seconds": seconds, "command_seconds": total,
            "command_wall_percent": 100 * total / seconds,
            "read_mib": delta["read_bytes"] / 2**20,
            "write_mib": delta["write_bytes"] / 2**20,
            "operations": {kind: {"count": delta[f"{kind}_count"],
                "seconds": delta[f"{kind}_ns"] / 1e9,
                "mean_us": delta[f"{kind}_ns"] / max(1, delta[f"{kind}_count"]) / 1000}
                for kind in ("read", "write", "flush", "other")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    report = {"storage": {}, "host_samples": {}}
    for path in sorted(args.directory.glob("*.stderr.log")):
        rows = [dict((k, int(v)) for k, v in re.findall(r"(\w+)=(\d+)", line))
                for line in path.read_text().splitlines() if "PROFILE elapsed_ns=" in line]
        if not rows:
            continue
        late = [row for row in rows if row["elapsed_ns"] >= 60e9]
        result = {"whole_observed_window": storage_window({}, rows[-1]),
                  "last_counters": rows[-1]}
        if len(late) > 1:
            result["after_60_seconds"] = storage_window(late[0], late[-1])
        report["storage"][path.name] = result
    for path in sorted(args.directory.glob("*.host*.txt")):
        report["host_samples"][path.name] = sample_tree(path)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
