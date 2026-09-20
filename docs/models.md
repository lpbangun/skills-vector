# Model and budget notes

Verified 2026-09-20 against DeepInfra public pages. No proprietary fallback.

| Role | Pinned id | License | DeepInfra list price (standard) | Structured JSON |
| --- | --- | --- | --- | --- |
| extract / map / default challenge | `deepseek-ai/DeepSeek-V4.1-Flash` | MIT | $0.20 / 1M in, $0.60 / 1M out, $0.006 cached | advertised |
| hard reconciliation / hard challenge (`escalate_hard`) | `zai-org/GLM-5.3` | Z.AI MIT-style; MaaS security review if licensee revenue > $10B | $1.20 / 1M in, $4.00 / 1M out, $0.20 cached | advertised |

`zai-org/GLM-5.3-Flash` ($0.15 / $0.50) exists and is cheaper, but this repo pins GLM-5.3 only for escalations so the default $10 month stays on DeepSeek-V4.1-Flash.

Live HTTP is off until `DEEPINFRA_API_KEY` is set **and** the DeepInfra interpreter is constructed with `live=True`. Budget reservations happen before every call. Exhaustion queues work and leaves the last approved release in place.

**Measured spend this slice:** US$0 (offline tests).

**Estimated example:** 2k input + 800 output tokens on Flash ≈ US$0.00088 before retries.
