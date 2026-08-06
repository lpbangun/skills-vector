# Lightweight project goal loop

## Goal

Build the smallest verified foundation that can produce one private, updatable, evidence-led U.S. role brief for the approved People Operations & Talent roles while preserving human judgment and uncertainty.

## Adaptive loop

At each meaningful increment:

1. Re-check scope: does the change directly strengthen the three-role private MVP or its verification?
2. Choose the highest-leverage bounded task that reduces a named risk or proves a product invariant.
3. Make the smallest coherent change, then verify behavior with offline tests or inspectable evidence.
4. Record a short result below and choose the next task from what the evidence revealed.

This is a decision loop, not a fixed backlog. Do not rewrite the loop or internal prompts in response to its own output.

## Guardrails and stop conditions

- Approved scope: U.S. only; People Operations & Talent; HR Coordinator, Recruiter, and Learning & Development Specialist.
- Approved evidence: public labor data, research papers, credible reports, selected job-posting signals, and official policy sources. Model benchmark news is not a labor-market leading indicator.
- Every claim must retain sources. High-impact claims need independent corroboration or visible disagreement.
- Role briefs remain private drafts unless a human explicitly approves publication. They describe role-level uncertainty and time-bound scenarios, never individual career predictions.
- LangGraph controls narrow modules and their tool boundaries. Agents never modify prompts, policy, memory, model routing, or code autonomously.
- Learning is limited to outcome evaluation, replay tests, and approved changes.
- The intended monitor cadence is weekly. A deep investigation runs only on a defined trigger or scheduled review; no scheduler is enabled in this phase.
- No deployment, publishing, scheduled runs, paid calls, or credential setup in this local phase.
- Stop when a coherent milestone is verified, the same blocker stalls progress twice, a decision or permission is required, or the $5 model experiment budget would be exceeded. Paid usage is $0 until the user explicitly configures credentials and approves a call.

## Progress evidence

| Date | Bounded increment | Evidence | Decision |
| --- | --- | --- | --- |
| 2026-08-03 | Initial repository and scope audit | Saved project was empty and not a Git repository | A minimal local foundation is appropriate; no user files to overwrite |
| 2026-08-03 | Domain and workflow contracts | 14 warning-free offline tests, bytecode compilation, dependency check, and forbidden-scope scan | Foundation milestone achieved; stop before provider, persistence, scheduler, or UI work |
| 2026-08-06 | Brief integrity: both horizons + unique claim ids | 16 offline unittest cases green; `validate_brief` rejects single-horizon drafts and duplicate claim ids | Keep briefs incomplete until near- and medium-term scenarios exist; stay inside domain validation |
