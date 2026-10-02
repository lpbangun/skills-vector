# Model and budget notes

Active occupational research is restricted to the authenticated DeepInfra
endpoint. The operator-owned config pins these exact model IDs by role:

| Runtime role | Exact DeepInfra model ID | Use |
| --- | --- | --- |
| `primary` | `deepseek-ai/DeepSeek-V4.1-Flash` | Primary research arm |
| `challenger` | `zai-org/GLM-5.3-Flash` | Same-corpus challenger arm |
| `escalation` | `zai-org/GLM-5.3` | Explicitly justified hard-case escalation |

There is no OMP market-inference lane, fixture fallback, silent model
substitution, or inference from public browsing/query/MCP requests. Model
agreement is not independent corroboration. Both candidate arms use the same
retrieved evidence; only a fresh independent adjudication can authorize a
candidate for local publication.

The research config, verified pricing receipt, durable mission budget ledger,
and run evidence live outside the product worktree. The config pins the
pricing-receipt SHA-256 and validates exact model identity, standard (not
promotional) price records, reservation, month, and resource ceilings. Prices
are therefore read from the verified receipt for that mission rather than
copied into source code or assumed from a live catalog page. `DEEPINFRA_API_KEY`
is supplied only through the local process environment; it is never stored in
the config, product, or deploy bundle.

The hard ceilings are 60 retrieval attempts, 12 model calls, 8,192 input
tokens, 2,048 output tokens, two retries, a 120-second model timeout, and a
30-minute run. The monthly account cap is at most US$10 and a mission allocation
at most US$2; each call is reserved before it can be sent. Actual usage and
remaining holds must be read from the durable ledger and receipts; estimates and
historical spend are not current accounting.
The reconciliation prompt limits the entire demand-claim array to four
representative claims across all postings, not four per posting or priority.
Claim counts are rendered from validated evidence IDs, not agent-authored signal
or detail text.
Original distinct expectation rows remain available; incomplete provider JSON is
rejected rather than salvaged or retried with a larger output ceiling.

An ambiguous billed attempt is never blindly repeated to recover its result.
`skills-vector market research reconcile` can settle it only when the exact
response bytes are retained inside that run and hash-match the attempt receipt.
Otherwise the unknown amount remains held for explicit provider/accounting
resolution. Reconciliation performs zero additional provider calls.

Routine refresh is also a local operator command, not an inference service or
scheduled job. It consumes the same reserved mission, bills every settled
provider attempt to that ledger, and reconciles the four current run receipts
against recorded actual usage before any pointer change. A previously
independently adjudicated aggregate retention record fixes the stage; primary-only
selection runs no challenger. Automatic refresh is never represented as fresh
human review, and any current challenger findings remain explicitly unresolved.
