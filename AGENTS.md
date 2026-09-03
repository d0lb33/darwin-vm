# Codex guidance for darwin-vm

Read `CLAUDE.md` in full before doing project work. It remains the canonical
project guide for layout, ownership, evidence standards, build and probe
commands, host-safety constraints, current status, and known false leads. Apply
all of those rules to Codex work.

## Custom agent routing

Project-scoped agents live under `.codex/agents/`:

| Agent | Use for | Tier |
|---|---|---|
| `deep-analyst` | hard cross-layer diagnosis and escalation | SOL / high |
| `device-modeler` | substantial QEMU device-model implementation | SOL / high |
| `hvf-prober` | consequential real-host and hypervisor experiments | SOL / high |
| `fw-miner` | firmware, kext, kernelcache, and device-tree RE | Terra / high |
| `ref-miner` | authoritative open-source reference mining | Terra / high |
| `loop-runner` | already-defined boot matrices and regression sweeps | Luna / low |
| `doc-writer` | narrow, evidence-backed documentation updates | Luna / low |

Use Terra/high for ordinary delegated work unless a named role is a better fit.
Reserve SOL/high for major implementation or analysis where errors are costly.
Use Luna/low only for tightly bounded, deterministic work with an explicit
input and output.

Delegate only independent, clearly bounded tasks. State what each agent owns,
whether the parent must wait, and what evidence or artifact it must return.
Avoid parallel edits in the same checkout. The Claude `isolation: worktree`
field has no direct project-agent TOML equivalent, so never assume a Codex
subagent has an isolated worktree; assign one explicitly when parallel code
changes require isolation.

For display runtime work, prefer the condition-bounded commands in
`CLAUDE.md` over blind multi-minute sleeps. Give `loop-runner` an explicit
variant matrix, unique tags/ports, stop condition, and required evidence. Use
`tools/re/setup_gate_sweep.sh` only when the variants are independent and the
host has enough memory; never overlap a QEMU build with a boot sweep.
