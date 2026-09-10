# P1.10 adapter handoff

Goal: run the unchanged seed loop through disposable Docker solver, gateway,
and business service containers; grade immutable submissions on the host using
a frozen, hardened grader-v1. Only task input and mechanical API access belong
inside the solver. The upstream checkout remains read-only.

Status: implemented. Twelve filtered service images passed 174 boundary checks;
all seven Section 5 adapter checks passed. Frozen grader-v1 passed all 120
reference controls and rejected the TG015/TG018 known wrong controls. Real
gpt-5-mini attempts on 013/train/001 and 017/train/001 both produced valid JSON
submissions and binary 0 (native and hardened scores 0.0); cost was $0.01652010.
Trajectories and ledgers are in logs/gdpevo/p110-{healthcare,legal}-*; full oracle
results are under oracle/ with matching run IDs. Containers/networks cleaned up.

The durable architecture, commands, evidence, costs, limitations, and Phase 3
follow-up are in `docs/gdpevo_adapter.md`. No harness changes or commits were
made by this task. New code uses the existing seed/environment interface.
