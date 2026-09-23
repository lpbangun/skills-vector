"""Controlled research workflow: collect → extract → reconcile → challenge → review.

Forecasting is optional and is never required to publish a skills reference.
Agents interpret; ordinary code retrieves, hashes, validates, and publishes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from .catalog import CatalogStore
from .ids import stable_id
from .interpret import (
    Interpreter,
    KeywordBaselineInterpreter,
    StructuredFixtureInterpreter,
    claims_from_extraction,
)
from .occupational import (
    FAMILY_BY_OCCUPATION,
    ONET_BASELINE,
    Occupation,
    OccupationId,
    Passage,
    ProficiencyLevel,
    ProficiencyRubric,
    RoleRequirement,
    RunPhase,
    RunStatus,
    Skill,
    SourceKind,
    Task,
    TaskSkillLink,
    WorkContext,
    ClaimStatus,
    EvidenceWeight,
)
from .forecast import attach_forecast
from .retrieval import Retriever, passages_from_document, source_from_document


PHASE_ORDER = (
    RunPhase.COLLECT,
    RunPhase.EXTRACT,
    RunPhase.RECONCILE,
    RunPhase.CHALLENGE,
    RunPhase.REVIEW,
)


@dataclass(frozen=True, slots=True)
class PilotSourceSpec:
    url: str
    kind: SourceKind
    weight: EvidenceWeight
    title: str
    publisher: str
    rights: str
    published_on: date | None = None


PILOT_SOURCES: dict[str, tuple[PilotSourceSpec, ...]] = {
    OccupationId.FOUNDING_ENGINEER.value: (
        PilotSourceSpec(
            "fixture://sources/founding_engineer_onet.json",
            SourceKind.ONET,
            EvidenceWeight.OCCUPATIONAL_BASELINE,
            "O*NET 15-1252.00 Software Developers tasks",
            "National Center for O*NET Development",
            "U.S. government work; O*NET content is public domain. Attribution retained.",
            date(2026, 1, 1),
        ),
        PilotSourceSpec(
            "fixture://sources/founding_engineer_job_ad.json",
            SourceKind.JOB_ADVERTISEMENT,
            EvidenceWeight.STATED_DEMAND,
            "Stated-demand founding engineer posting (fixture)",
            "Fixture Board",
            "offline fixture; not a live employer source",
        ),
    ),
    OccupationId.PRODUCT_MANAGER.value: (
        PilotSourceSpec(
            "fixture://sources/product_manager_onet.json",
            SourceKind.ONET,
            EvidenceWeight.OCCUPATIONAL_BASELINE,
            "O*NET 15-1299.09 IT Project Managers tasks",
            "National Center for O*NET Development",
            "U.S. government work; O*NET content is public domain. Attribution retained.",
            date(2026, 1, 1),
        ),
    ),
    OccupationId.GROWTH_OPERATOR.value: (
        PilotSourceSpec(
            "fixture://sources/growth_operator_onet.json",
            SourceKind.ONET,
            EvidenceWeight.OCCUPATIONAL_BASELINE,
            "O*NET 13-1161.00 Market Research Analysts tasks",
            "National Center for O*NET Development",
            "U.S. government work; O*NET content is public domain. Attribution retained.",
            date(2026, 1, 1),
        ),
    ),
}

LIVE_SOURCES: dict[str, tuple[PilotSourceSpec, ...]] = {
    OccupationId.FOUNDING_ENGINEER.value: (
        PilotSourceSpec(
            "https://www.onetonline.org/link/summary/15-1252.00",
            SourceKind.ONET,
            EvidenceWeight.OCCUPATIONAL_BASELINE,
            "O*NET 15-1252.00 Software Developers",
            "National Center for O*NET Development",
            "U.S. government work; O*NET content is public domain. Attribution retained.",
        ),
    ),
    OccupationId.PRODUCT_MANAGER.value: (
        PilotSourceSpec(
            "https://www.onetonline.org/link/summary/15-1299.09",
            SourceKind.ONET,
            EvidenceWeight.OCCUPATIONAL_BASELINE,
            "O*NET 15-1299.09 Information Technology Project Managers",
            "National Center for O*NET Development",
            "U.S. government work; O*NET content is public domain. Attribution retained.",
        ),
    ),
    OccupationId.GROWTH_OPERATOR.value: (
        PilotSourceSpec(
            "https://www.onetonline.org/link/summary/13-1161.00",
            SourceKind.ONET,
            EvidenceWeight.OCCUPATIONAL_BASELINE,
            "O*NET 13-1161.00 Market Research Analysts and Marketing Specialists",
            "National Center for O*NET Development",
            "U.S. government work; O*NET content is public domain. Attribution retained.",
        ),
    ),
}


@dataclass
class ResearchPipeline:
    store: CatalogStore
    fixture_root: Path
    interpreter: Interpreter
    retriever_factory: Callable[[bool], Retriever] | None = None
    include_forecast: bool = False

    def run(
        self,
        occupation_id: str,
        *,
        allow_fixtures: bool,
        resume_run_id: str | None = None,
        reviewer_note: str = "",
    ) -> str:
        if occupation_id not in {item.value for item in OccupationId}:
            raise ValueError("occupation is outside the three-role pilot")
        if resume_run_id:
            run = self.store.run(resume_run_id)
            if run.status is RunStatus.REJECTED:
                raise ValueError("rejected runs are terminal; start a new run")
            run_id = run.run_id
        else:
            run = self.store.start_run(occupation_id, allow_fixtures=allow_fixtures, forecast_included=self.include_forecast)
            run_id = run.run_id
            self._seed_occupation(occupation_id)

        # Interpreters that reserve budget per run (e.g. DeepInfra) are constructed
        # before the run exists; bind the real run id so ledger entries line up.
        if hasattr(self.interpreter, "run_id"):
            try:
                self.interpreter.run_id = run_id  # type: ignore[attr-defined]
            except AttributeError:
                pass

        start_index = PHASE_ORDER.index(run.phase)
        if resume_run_id and run.status is RunStatus.CHANGES_REQUESTED:
            start_index = PHASE_ORDER.index(RunPhase.CHALLENGE)
        if resume_run_id and run.status is RunStatus.AWAITING_REVIEW:
            return run_id

        self._graph().compile()
        state: dict[str, Any] = {
            "run_id": run_id,
            "occupation_id": occupation_id,
            "allow_fixtures": allow_fixtures,
            "reviewer_note": reviewer_note,
        }
        latest = self.store.latest_checkpoint(run_id)
        if latest:
            state.update(latest[1])
            state["run_id"] = run_id

        for phase in PHASE_ORDER[start_index:]:
            if phase is RunPhase.REVIEW:
                self.store.set_run(run_id, status=RunStatus.AWAITING_REVIEW, phase=RunPhase.REVIEW)
                self.store.write_checkpoint(run_id, phase, state)
                break
            handler = getattr(self, f"_{phase.value}")
            try:
                updates = handler(state)
            except Exception:
                self.store.set_run(run_id, status=RunStatus.FAILED, phase=phase)
                raise
            state.update(updates)
            self.store.write_checkpoint(run_id, phase, {k: v for k, v in state.items() if k != "interpreter"})
            next_index = PHASE_ORDER.index(phase) + 1
            next_phase = PHASE_ORDER[next_index] if next_index < len(PHASE_ORDER) else phase
            self.store.set_run(run_id, phase=next_phase, status=RunStatus.RUNNING)
        return run_id

    def _graph(self):
        graph = StateGraph(dict)
        graph.add_node("collect", self._collect)
        graph.add_node("extract", self._extract)
        graph.add_node("reconcile", self._reconcile)
        graph.add_node("challenge", self._challenge)
        graph.add_edge(START, "collect")
        graph.add_edge("collect", "extract")
        graph.add_edge("extract", "reconcile")
        graph.add_edge("reconcile", "challenge")
        graph.add_edge("challenge", END)
        return graph

    def _seed_occupation(self, occupation_id: str) -> None:
        occ = OccupationId(occupation_id)
        onet_code, onet_title = ONET_BASELINE[occ]
        family = FAMILY_BY_OCCUPATION[occ]
        titles = {
            OccupationId.FOUNDING_ENGINEER: "Founding Engineer",
            OccupationId.PRODUCT_MANAGER: "Product Manager",
            OccupationId.GROWTH_OPERATOR: "Growth Operator",
        }
        aliases = {
            OccupationId.FOUNDING_ENGINEER: ("Founding Software Engineer", "First Engineer"),
            OccupationId.PRODUCT_MANAGER: ("PM", "Product Lead"),
            OccupationId.GROWTH_OPERATOR: ("Growth Manager", "Growth Lead"),
        }
        self.store.upsert_occupation(
            Occupation(
                occ.value,
                occ.value.removeprefix("occ_"),
                titles[occ],
                family,
                onet_code=onet_code,
                onet_title=onet_title,
                aliases=aliases[occ],
            )
        )
        self.store.put_context(
            WorkContext(
                stable_id("ctx", occ.value, "seed"),
                occ.value,
                "seed_to_series_a",
                tools=_default_tools(occ),
            )
        )

    def _collect(self, state: dict[str, Any]) -> dict[str, Any]:
        occupation_id = state["occupation_id"]
        allow_fixtures = bool(state["allow_fixtures"])
        retriever = (self.retriever_factory or (lambda allowed: Retriever(self.fixture_root, allowed)))(allow_fixtures)
        source_ids: list[str] = []
        passage_ids: list[str] = []
        specs = PILOT_SOURCES[occupation_id] if allow_fixtures else LIVE_SOURCES[occupation_id]
        for spec in specs:
            document = retriever.retrieve(spec.url)
            source = source_from_document(
                document,
                kind=spec.kind,
                weight=spec.weight,
                title=spec.title,
                publisher=spec.publisher,
                published_on=spec.published_on,
                rights=spec.rights,
            )
            created = self.store.put_source(source)
            source_ids.append(source.source_id)
            if document.status != "ok":
                continue
            for passage in passages_from_document(source, document):
                self.store.put_passage(passage)
                passage_ids.append(passage.passage_id)
            if document.url.startswith("fixture://") and document.body:
                raw = Passage(
                    stable_id("psg", source.source_id, "raw_json"),
                    source.source_id,
                    "raw_json",
                    document.body,
                )
                self.store.put_passage(raw)
                passage_ids.append(raw.passage_id)
            _ = created
        return {"source_ids": source_ids, "passage_ids": passage_ids}

    def _extract(self, state: dict[str, Any]) -> dict[str, Any]:
        occupation_id = state["occupation_id"]
        sources = tuple(s for s in self.store.sources() if s.source_id in set(state.get("source_ids", ())))
        passages = tuple(p for p in self.store.passages() if p.passage_id in set(state.get("passage_ids", ())))
        extraction = self.interpreter.extract(occupation_id, sources, passages)
        claims = claims_from_extraction(occupation_id, extraction, passages, sources)
        for claim in claims:
            self.store.put_claim(claim)
        return {
            "extraction_task_count": len(extraction.tasks),
            "claim_ids": [claim.claim_id for claim in claims],
            "extraction_skills": dict(extraction.skills),
            "extracted_tasks": [
                {
                    "statement": task.statement,
                    "output": task.output,
                    "success_criteria": task.success_criteria,
                    "frequency": task.frequency,
                    "criticality": task.criticality,
                    "skill_names": list(task.skill_names),
                    "passage_locator": task.passage_locator,
                }
                for task in extraction.tasks
            ],
        }

    def _reconcile(self, state: dict[str, Any]) -> dict[str, Any]:
        from .interpret import ExtractionResult, ExtractedTask

        occupation_id = state["occupation_id"]
        extraction = ExtractionResult(
            tuple(
                ExtractedTask(
                    item["statement"],
                    item["output"],
                    item["success_criteria"],
                    item["frequency"],
                    item["criticality"],
                    tuple(item.get("skill_names") or ()),
                    item.get("passage_locator", ""),
                )
                for item in state.get("extracted_tasks", ())
            ),
            tuple(state.get("extraction_skills", {}).items()),
            self.interpreter.name,
            "extract.v1",
        )
        result = self.interpreter.reconcile(occupation_id, extraction)
        context_id = self.store.contexts(occupation_id)[0].context_id
        for name, description in extraction.skills:
            skill_id = result.canonical_skills.get(name.casefold()) or result.canonical_skills.get(name, "")
            if not skill_id:
                continue
            self.store.upsert_skill(Skill(skill_id, name, description))
            rubric = ProficiencyRubric(
                stable_id("rbr", occupation_id, skill_id),
                skill_id,
                occupation_id,
                1,
                {
                    ProficiencyLevel.AWARENESS.value: "Can explain the skill and its startup constraints.",
                    ProficiencyLevel.WORKING.value: "Completes standard tasks with review.",
                    ProficiencyLevel.INDEPENDENT.value: "Delivers the task under ambiguity without close supervision.",
                    ProficiencyLevel.LEADING.value: "Sets the bar and teaches others in this context.",
                },
                provisional=True,
            )
            self.store.put_rubric(rubric)
        for index, item in enumerate(state.get("extracted_tasks", ())):
            task_id = stable_id("tsk", occupation_id, item["statement"])
            task = Task(
                task_id,
                occupation_id,
                item["statement"],
                item["output"],
                item["success_criteria"],
                item["frequency"],
                item["criticality"],
                (),
                context_id,
            )
            self.store.upsert_task(task)
            for skill_name in item.get("skill_names", ()):
                skill_id = result.canonical_skills.get(skill_name.casefold()) or result.canonical_skills.get(skill_name)
                if not skill_id:
                    continue
                self.store.put_task_skill(TaskSkillLink(task_id, skill_id))
                rubric_id = stable_id("rbr", occupation_id, skill_id)
                self.store.upsert_requirement(
                    RoleRequirement(
                        stable_id("req", occupation_id, skill_id),
                        occupation_id,
                        skill_id,
                        (task_id,),
                        ProficiencyLevel.INDEPENDENT,
                        rubric_id,
                        context_id,
                    )
                )
            _ = index
        return {"canonical_skills": result.canonical_skills, "model_agreement": result.model_agreement}

    def _challenge(self, state: dict[str, Any]) -> dict[str, Any]:
        occupation_id = state["occupation_id"]
        claims = self.store.claims(occupation_id)
        passages = self.store.passages()
        sources = self.store.sources()
        result = self.interpreter.challenge(claims, passages, sources)
        for claim in claims:
            status = result.claim_statuses.get(claim.claim_id, ClaimStatus.UNCERTAIN)
            note = result.notes.get(claim.claim_id, claim.uncertainty_note)
            disagreement = note if status is ClaimStatus.DISPUTED else claim.disagreement_note
            updated = ClaimStatus(status)
            from .occupational import Claim as ClaimModel

            self.store.put_claim(
                ClaimModel(
                    claim.claim_id,
                    claim.occupation_id,
                    claim.statement,
                    claim.claim_type,
                    claim.passage_ids,
                    claim.source_ids,
                    updated,
                    note,
                    disagreement,
                    result.model_agreement,
                    False,
                    claim.prompt_version,
                    claim.model_id,
                )
            )
        return {
            "challenge_notes": result.notes,
            "forecast_included": self.include_forecast,
            "forecast": attach_forecast(occupation_id, tuple(source.source_id for source in sources)).statement
            if self.include_forecast
            else None,
        }


def default_fixture_root() -> Path:
    return Path(__file__).parent / "fixtures"


def build_offline_pipeline(store: CatalogStore, *, baseline: bool = False) -> ResearchPipeline:
    interpreter: Interpreter = KeywordBaselineInterpreter() if baseline else StructuredFixtureInterpreter()
    return ResearchPipeline(store, default_fixture_root(), interpreter)


def _default_tools(occupation: OccupationId) -> tuple[str, ...]:
    return {
        OccupationId.FOUNDING_ENGINEER: ("git", "python", "observability", "llm_api"),
        OccupationId.PRODUCT_MANAGER: ("issue_tracker", "analytics", "figma"),
        OccupationId.GROWTH_OPERATOR: ("analytics", "experimentation", "crm"),
    }[occupation]
