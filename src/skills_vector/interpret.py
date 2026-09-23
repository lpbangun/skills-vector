"""Interpretation adapters: bounded structured outputs only. Retrieval stays in code."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from .budget import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    MAX_RETRIES,
    PINNED_MODELS,
    BudgetError,
    BudgetLedger,
    estimate_cost_usd,
)
from .ids import stable_id
from .occupational import (
    PROMPT_VERSIONS,
    Claim,
    ClaimStatus,
    OccupationId,
    Passage,
    SourceRecord,
)


@dataclass(frozen=True, slots=True)
class ExtractedTask:
    statement: str
    output: str
    success_criteria: str
    frequency: str
    criticality: str
    skill_names: tuple[str, ...]
    passage_locator: str


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    tasks: tuple[ExtractedTask, ...]
    skills: tuple[tuple[str, str], ...]
    model_id: str
    prompt_version: str
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    canonical_skills: dict[str, str]
    new_concepts: tuple[str, ...]
    model_id: str
    prompt_version: str
    model_agreement: bool = False


@dataclass(frozen=True, slots=True)
class ChallengeResult:
    claim_statuses: dict[str, ClaimStatus]
    notes: dict[str, str]
    model_id: str
    prompt_version: str
    model_agreement: bool = False


class Interpreter(Protocol):
    name: str

    def extract(self, occupation_id: str, sources: tuple[SourceRecord, ...], passages: tuple[Passage, ...]) -> ExtractionResult: ...
    def reconcile(self, occupation_id: str, extraction: ExtractionResult) -> ReconciliationResult: ...
    def challenge(self, claims: tuple[Claim, ...], passages: tuple[Passage, ...], sources: tuple[SourceRecord, ...]) -> ChallengeResult: ...


class StructuredFixtureInterpreter:
    """Offline path: reads labeled fixture JSON. Never pretends this is live retrieval."""

    name = "offline-structured-fixture"

    def extract(self, occupation_id: str, sources: tuple[SourceRecord, ...], passages: tuple[Passage, ...]) -> ExtractionResult:
        tasks: list[ExtractedTask] = []
        skills: dict[str, str] = {}
        for source in sources:
            if not source.fixture or not source.retrieval_ok:
                continue
            payload = _fixture_payload(source, passages)
            if payload.get("occupation_id") != occupation_id:
                continue
            for item in payload.get("labeled_tasks", ()):
                tasks.append(
                    ExtractedTask(
                        item["statement"],
                        item["output"],
                        item["success_criteria"],
                        item.get("frequency", "recurring"),
                        item.get("criticality", "core"),
                        tuple(item.get("skill_names") or ()),
                        item.get("passage_locator", "labeled_tasks"),
                    )
                )
            for skill in payload.get("labeled_skills", ()):
                skills[skill["name"]] = skill["description"]
        if not tasks:
            for passage in passages:
                for statement in scan_task_sentences(passage.text):
                    tasks.append(
                        ExtractedTask(
                            statement,
                            "unspecified pending review",
                            "Reviewer must confirm success criteria.",
                            "unknown",
                            "core",
                            (),
                            passage.locator,
                        )
                    )
        return ExtractionResult(tuple(tasks), tuple(skills.items()), self.name, PROMPT_VERSIONS["extract"])

    def reconcile(self, occupation_id: str, extraction: ExtractionResult) -> ReconciliationResult:
        canonical: dict[str, str] = {}
        for name, _description in extraction.skills:
            canonical[name.casefold()] = _slug_skill(name)
        for task in extraction.tasks:
            for name in task.skill_names:
                canonical.setdefault(name.casefold(), _slug_skill(name))
        return ReconciliationResult(canonical, tuple(sorted(canonical.values())), self.name, PROMPT_VERSIONS["reconcile"])

    def challenge(
        self,
        claims: tuple[Claim, ...],
        passages: tuple[Passage, ...],
        sources: tuple[SourceRecord, ...],
    ) -> ChallengeResult:
        by_passage = {passage.passage_id: passage for passage in passages}
        statuses: dict[str, ClaimStatus] = {}
        notes: dict[str, str] = {}
        source_by_id = {source.source_id: source for source in sources}
        for claim in claims:
            if not claim.passage_ids:
                statuses[claim.claim_id] = ClaimStatus.UNSUPPORTED
                notes[claim.claim_id] = "No supporting passages were supplied to the challenge step."
                continue
            missing = [pid for pid in claim.passage_ids if pid not in by_passage]
            if missing:
                statuses[claim.claim_id] = ClaimStatus.UNSUPPORTED
                notes[claim.claim_id] = "Cited passages are missing from the evidence corpus."
                continue
            ads_only = all(source_by_id[sid].kind.value == "job_advertisement" for sid in claim.source_ids if sid in source_by_id)
            if ads_only and claim.claim_type == "requirement":
                statuses[claim.claim_id] = ClaimStatus.DISPUTED
                notes[claim.claim_id] = "Job advertisements indicate stated demand, not proof of actual work."
                continue
            conflicting = any("CONTRADICTION" in by_passage[pid].text for pid in claim.passage_ids)
            if conflicting:
                statuses[claim.claim_id] = ClaimStatus.DISPUTED
                notes[claim.claim_id] = "A supplied passage marks a contradiction; disagreement is preserved."
                continue
            statuses[claim.claim_id] = ClaimStatus.SUPPORTED
            notes[claim.claim_id] = "Passages were present; this is not independent corroboration."
        return ChallengeResult(statuses, notes, self.name, PROMPT_VERSIONS["challenge"])


class KeywordBaselineInterpreter:
    """Simple extraction baseline for evaluation. Not used for publication."""

    name = "keyword-baseline"

    def extract(self, occupation_id: str, sources: tuple[SourceRecord, ...], passages: tuple[Passage, ...]) -> ExtractionResult:
        lexicon = {
            "python": "Write and review Python",
            "sql": "Query data with SQL",
            "experiment": "Design growth experiments",
            "roadmap": "Sequence product work",
            "customer": "Talk with customers",
            "software": "Design and develop software",
        }
        tasks: list[ExtractedTask] = []
        skills: dict[str, str] = {}
        for passage in passages:
            haystack = passage.text.casefold()
            hits = [name for name in lexicon if name in haystack]
            if not hits:
                continue
            statement = passage.text.strip().split(".")[0][:240]
            tasks.append(
                ExtractedTask(
                    statement or "Unscoped extracted fragment",
                    "unspecified",
                    "baseline has no success criteria",
                    "unknown",
                    "unknown",
                    tuple(hits),
                    passage.locator,
                )
            )
            for hit in hits:
                skills[hit] = lexicon[hit]
        return ExtractionResult(tuple(tasks), tuple(skills.items()), self.name, "baseline.v1")

    def reconcile(self, occupation_id: str, extraction: ExtractionResult) -> ReconciliationResult:
        return ReconciliationResult(
            {name: _slug_skill(name) for name, _ in extraction.skills},
            (),
            self.name,
            "baseline.v1",
        )

    def challenge(self, claims: tuple[Claim, ...], passages: tuple[Passage, ...], sources: tuple[SourceRecord, ...]) -> ChallengeResult:
        return ChallengeResult({claim.claim_id: ClaimStatus.UNCERTAIN for claim in claims}, {}, self.name, "baseline.v1")


class DeepInfraInterpreter:
    """Budgeted DeepInfra client. Live HTTP is disabled unless credentials and live=True."""

    name = "deepinfra"

    def __init__(
        self,
        ledger: BudgetLedger,
        *,
        run_id: str,
        api_key: str | None,
        live: bool = False,
        escalate_hard: bool = False,
        http_post=None,
    ) -> None:
        self.ledger = ledger
        self.run_id = run_id
        self.api_key = api_key
        self.live = live
        self.escalate_hard = escalate_hard
        self._http_post = http_post

    def extract(self, occupation_id: str, sources: tuple[SourceRecord, ...], passages: tuple[Passage, ...]) -> ExtractionResult:
        payload = self._complete("extract", PINNED_MODELS["extract"].model_id, {
            "occupation_id": occupation_id,
            "passages": [{"passage_id": p.passage_id, "text": p.text, "locator": p.locator} for p in passages],
            "instruction": "Identify tasks, skills, tools, context, and supporting passages. JSON only.",
        })
        tasks = tuple(
            ExtractedTask(
                item["statement"],
                item.get("output", ""),
                item.get("success_criteria", ""),
                item.get("frequency", "recurring"),
                item.get("criticality", "core"),
                tuple(item.get("skill_names") or ()),
                item.get("passage_locator", ""),
            )
            for item in payload.get("tasks", ())
        )
        skills = tuple((item["name"], item.get("description", "")) for item in payload.get("skills", ()))
        return ExtractionResult(tasks, skills, PINNED_MODELS["extract"].model_id, PROMPT_VERSIONS["extract"], payload.get("_cost_usd", 0.0))

    def reconcile(self, occupation_id: str, extraction: ExtractionResult) -> ReconciliationResult:
        payload = self._complete("reconcile", PINNED_MODELS["reconcile"].model_id, {
            "occupation_id": occupation_id,
            "skills": [{"name": name, "description": description} for name, description in extraction.skills],
            "instruction": "Resolve terminology. Do not treat model agreement as source corroboration.",
        })
        return ReconciliationResult(
            payload.get("canonical_skills", {}),
            tuple(payload.get("new_concepts") or ()),
            PINNED_MODELS["reconcile"].model_id,
            PROMPT_VERSIONS["reconcile"],
            bool(payload.get("model_agreement", False)),
        )

    def challenge(self, claims: tuple[Claim, ...], passages: tuple[Passage, ...], sources: tuple[SourceRecord, ...]) -> ChallengeResult:
        price = PINNED_MODELS["challenge_hard"] if self.escalate_hard else PINNED_MODELS["challenge"]
        payload = self._complete("challenge", price.model_id, {
            "claims": [
                {
                    "claim_id": claim.claim_id,
                    "statement": claim.statement,
                    "claim_type": claim.claim_type,
                    "passage_ids": claim.passage_ids,
                    "uncertainty_note": claim.uncertainty_note,
                }
                for claim in claims
            ],
            "passages": [{"passage_id": p.passage_id, "text": p.text} for p in passages],
            "instruction": "Challenge each claim using its passages and any contradictory evidence. JSON only.",
        })
        statuses = {
            claim_id: ClaimStatus(status)
            for claim_id, status in payload.get("claim_statuses", {}).items()
        }
        return ChallengeResult(
            statuses,
            payload.get("notes", {}),
            price.model_id,
            PROMPT_VERSIONS["challenge"],
            bool(payload.get("model_agreement", False)),
        )

    def _complete(self, operation: str, model_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.live:
            raise RuntimeError("DeepInfra live calls are disabled; use the offline fixture interpreter")
        if not self.api_key:
            raise RuntimeError("DEEPINFRA_API_KEY is required for live calls and must stay out of source control")
        price = next(item for item in PINNED_MODELS.values() if item.model_id == model_id)
        estimate = estimate_cost_usd(price, input_tokens=len(json.dumps(payload)) // 4, output_tokens=DEFAULT_MAX_OUTPUT_TOKENS)
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            reservation_id = self.ledger.reserve(
                run_id=self.run_id, model_id=model_id, estimated_usd=max(estimate, 0.0001), note=operation
            )
            try:
                body = {
                    "model": model_id,
                    "messages": [
                        {"role": "system", "content": "Return one JSON object. No tools. No source retrieval."},
                        {"role": "user", "content": json.dumps(payload, sort_keys=True)},
                    ],
                    "max_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
                    "response_format": {"type": "json_object"},
                }
                raw = self._http_post(
                    "https://api.deepinfra.com/v1/openai/chat/completions",
                    body,
                    self.api_key,
                ) if self._http_post else _post_json(
                    "https://api.deepinfra.com/v1/openai/chat/completions", body, self.api_key
                )
                usage = raw.get("usage") or {}
                input_tokens = int(usage.get("prompt_tokens") or 0)
                output_tokens = int(usage.get("completion_tokens") or 0)
                actual = estimate_cost_usd(price, input_tokens=input_tokens or 1, output_tokens=output_tokens or 1)
                self.ledger.settle(reservation_id, actual_usd=actual, input_tokens=input_tokens, output_tokens=output_tokens)
                content = raw["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                parsed["_cost_usd"] = actual
                return parsed
            except BudgetError:
                raise
            except (KeyError, IndexError, json.JSONDecodeError, RuntimeError, OSError) as exc:
                last_error = exc
                self.ledger.release(reservation_id, note=f"malformed or failed attempt {attempt}")
                if attempt >= MAX_RETRIES:
                    raise ValueError(f"malformed or failed model response after retries: {exc}") from exc
        raise RuntimeError(last_error)


def _post_json(url: str, body: dict[str, Any], api_key: str) -> dict[str, Any]:
    from urllib.request import Request, urlopen

    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _fixture_payload(source: SourceRecord, passages: tuple[Passage, ...]) -> dict[str, Any]:
    for passage in passages:
        if passage.source_id == source.source_id and passage.locator == "raw_json":
            return json.loads(passage.text)
    # Reconstruct from original fixture file content stored as first JSON passage set.
    joined = "\n".join(passage.text for passage in passages if passage.source_id == source.source_id)
    try:
        return json.loads(joined)
    except json.JSONDecodeError:
        return {}


def _slug_skill(name: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in name).strip("_")
    return stable_id("skl", slug) if not slug else f"skl_{slug[:40]}"


def scan_task_sentences(text: str) -> tuple[str, ...]:
    """Ordinary-code baseline: pull imperative task-like sentences from O*NET-style text."""

    found: list[str] = []
    for raw in text.replace("\n", " ").split("."):
        sentence = raw.strip()
        if len(sentence) < 40:
            continue
        if sentence[0].isupper() and any(
            sentence.startswith(prefix)
            for prefix in ("Analyze", "Develop", "Modify", "Manage", "Collect", "Measure", "Confer", "Plan", "Research")
        ):
            found.append(sentence[:300])
    return tuple(found)


def claims_from_extraction(
    occupation_id: str,
    extraction: ExtractionResult,
    passages: tuple[Passage, ...],
    sources: tuple[SourceRecord, ...],
) -> tuple[Claim, ...]:
    by_locator = {passage.locator: passage for passage in passages}
    source_ids = tuple(source.source_id for source in sources if source.retrieval_ok)
    claims: list[Claim] = []
    for task in extraction.tasks:
        passage = by_locator.get(task.passage_locator) or (passages[0] if passages else None)
        if passage is None:
            continue
        claim_id = stable_id("clm", occupation_id, task.statement, passage.passage_id)
        claims.append(
            Claim(
                claim_id,
                occupation_id,
                task.statement,
                "task",
                (passage.passage_id,),
                (passage.source_id,) if passage.source_id else source_ids[:1],
                ClaimStatus.PROPOSED,
                "Extracted from labeled or model-proposed passages; not independently corroborated.",
                "",
                False,
                False,
                extraction.prompt_version,
                extraction.model_id,
            )
        )
    return tuple(claims)
