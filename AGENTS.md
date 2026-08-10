# Working guidance

- Inspect the repository and current behavior before changing it; reason from observed evidence, not assumptions.
- Choose the simplest coherent path to the user-visible outcome. Prefer reversible, surgical changes, while allowing a broader refactor when evidence shows it is the clearer solution.
- Verify behavior directly at the boundary the user will experience. Tests, runtime output, and persisted state are stronger evidence than plans or static structure.
- Keep progress reports honest: distinguish implemented, verified, deferred, and uncertain work. Never declare success from scaffolding alone.
- Surface tradeoffs only when they are meaningful to outcome, risk, or future change; avoid ceremony and speculative scope.
- Treat [SPEC.md](SPEC.md) as the living product outcome and [README.md](README.md) as the authoritative run/verification guide. Architecture and commands may evolve when direct evidence supports a better design.
