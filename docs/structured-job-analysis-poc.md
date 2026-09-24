# POC A — Structured Job Analysis (round 1)

This is a local-only US individual-contributor HR Generalist role guide with a responsibility-based People Operations title variant. O*NET 13-1071.00 and OPM job-analysis guidance anchor an authored task–competency backbone. DACUM informs only the duty/task decomposition structure: this is desk research, not a DACUM workshop or practitioner-validated analysis.

Employer postings remain a separate counts-only demand layer or explicitly labeled context additions. The frozen v2 evidence is a nonrepresentative public Greenhouse convenience sample. Its demand threshold is **INSUFFICIENT** (14 unique admitted items overall; 7 dev), so no prevalence percentage is allowed. Posting counts are not hires, hiring probability, occupational importance, or proficiency. The literal title “People Operations” does not occur in the seven dev posting titles; the guide treats it only as a cautious responsibility-based functional variant, not a claim of title equivalence.

## Build and verify offline

No credentials or network requests are used. Run from the repository root with Python 3.12:

```sh
python -m skills_vector structured-job-analysis \
  --mode offline \
  --benchmark-dir /home/logani/oprun-evidence/skills-vector-poc-prep-74b159c/shared-benchmark \
  --base-corpus-dir /home/logani/oprun-evidence/skills-vector-poc-prep-74b159c/corpus \
  --output-dir outputs/structured-job-analysis-round1
```

The pipeline verifies pinned manifest hashes, all v1 source/extract bytes, all seven public dev posting raw/extract hashes, and the public dev split; it does not open or enumerate `held-out/`. Every emitted quote must occur byte-for-byte in the admitted extract. Output paths:

- `outputs/structured-job-analysis-round1/release.json` — machine output (`skills-vector.poc-output.v1`)
- `outputs/structured-job-analysis-round1/guide.md` — standalone Markdown guide
- `outputs/structured-job-analysis-round1/guide.html` — local, self-contained browser-readable companion
- `outputs/structured-job-analysis-round1/run-manifest.json` — model/config/caps/evidence-policy record
- `outputs/structured-job-analysis-round1/run-receipt.json` — command, exit status, hashes, and resource receipt

Offline content and unit IDs are deterministic for a fixed candidate SHA and corpus. Runtime timestamps and measured wall time naturally differ. The O*NET task-rating source is a dictionary page, not HR-specific downloaded rating records; all proficiency fields say `desk-research` and assign no numeric level. Phrase-screen coverage is a deterministic, conservative string-match proxy, not semantic duty coding; a miss is not evidence of absence.

Open the local guide in a browser with `file:///.../outputs/structured-job-analysis-round1/guide.html`. It contains no remote assets or scripts. Example agent consumer:

```sh
python examples/consume_structured_job_analysis.py outputs/structured-job-analysis-round1/release.json
```

Run product and POC tests:

```sh
python -m unittest discover -s tests -v
python -m compileall -q src tests examples
```

The frozen shared acceptance files are read-only. Candidate contract, citation, and corpus tests are therefore invoked individually; do **not** run `shared-benchmark/tests/run_acceptance.py` from this worktree because its runner writes a receipt into the frozen benchmark tree. Do not run the split-integrity test in this lane because it probes the sealed held-out tree.

## One guarded final live mode — not used in round 1

The live mode is designed for one post-freeze execution only. It is unavailable without all of: the exact full candidate `HEAD` in `--freeze-sha`, `--confirm-final-run`, a parent-approved `--resource-config`, and `DEEPINFRA_API_KEY` supplied only through the environment. The model/provider are pinned to `deepseek-ai/DeepSeek-V4.1-Flash` / DeepInfra. Resource config must keep retrieval at zero and `max_inference_cost_usd <= 0.50`. It records sampling and all caps; the parent must ensure those values match the other lane before freezing. The config format is:

```json
{
  "model": "deepseek-ai/DeepSeek-V4.1-Flash",
  "provider": "deepinfra",
  "sampling": {"temperature": 0.0, "top_p": 1.0, "max_tokens": 2048},
  "caps": {
    "max_inference_requests": 1,
    "max_inference_cost_usd": 0.50,
    "max_retrieval_requests": 0,
    "max_wall_minutes": 15
  }
}
```

The JSON values above show the required shape and a conservative one-request example; they are not a substitute for parent approval of matched sampling/resource settings. The live mode preflights the **sum** of all planned requests against both configured caps and the hard USD 0.50 ceiling before sending any request. The current plan contains one citation-grounded review request. There is no retry, escalation, fallback, or out-of-corpus retrieval. A persistent one-shot marker at `.poc-env/state/structured-job-analysis-final-live.json` is created before the provider request; a failed request consumes the sole attempt and cannot be retried through the normal CLI. The model review is untrusted, retained separately, and never changes the authored guide or becomes validation.

After parent candidate freeze and approval only:

```sh
python -m skills_vector structured-job-analysis \
  --mode live-final \
  --resource-config /absolute/path/to/parent-approved-a-resource-config.json \
  --freeze-sha <full-frozen-candidate-sha> \
  --confirm-final-run
```

This implementation pass made no provider call and used no credentials. Do not create or run a live config until the parent freezes the candidate and confirms matched resource settings.
