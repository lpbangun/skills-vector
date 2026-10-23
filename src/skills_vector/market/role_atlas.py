"""Role Atlas role/2 contract and read-only catalog, separate from published market releases."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields, is_dataclass
from decimal import Decimal
from functools import cache
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Self, get_args, get_origin, get_type_hints

from jsonschema import Draft202012Validator

SCHEMA_VERSION = "skills-vector.role/2"
CONTRACT_ROOT = Path(__file__).with_name("role_atlas_contract")
SCHEMA_PATH = CONTRACT_ROOT / "role.schema.json"
FIXTURE_ROOT = CONTRACT_ROOT / "fixtures"
Timing = Literal["leading", "coincident", "lagging", "long-term"]
Family = Literal["P", "O", "M", "context"]
SkillStatus = Literal["core", "rising", "early", "fading"]
Direction = Literal["up", "down", "flat", "presence"]
SourceKind = Literal[
    "postings", "postings_new_phrasing", "onet_ratings", "onet_emerging_tasks",
    "onet_hot_technologies", "esco", "hiring_lab", "economic_index",
    "bls_projections", "bls_industry_matrix", "careeronestop",
]
SOURCE_RULES: dict[SourceKind, tuple[Timing, Family]] = {
    "postings": ("coincident", "P"),
    "postings_new_phrasing": ("leading", "P"),
    "onet_ratings": ("lagging", "O"),
    "onet_emerging_tasks": ("leading", "O"),
    "onet_hot_technologies": ("coincident", "M"),
    "esco": ("lagging", "O"),
    "hiring_lab": ("coincident", "M"),
    "economic_index": ("coincident", "M"),
    "bls_projections": ("long-term", "context"),
    "bls_industry_matrix": ("lagging", "context"),
    "careeronestop": ("lagging", "O"),
}


class RoleValidationError(ValueError):
    """A structural or evidence-rule violation in a role/2 record."""


@cache
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


@cache
def _model_hints(kind: type) -> dict[str, Any]:
    return get_type_hints(kind)


def _decode(kind: Any, value: Any) -> Any:
    if value is None:
        return None
    origin, args = get_origin(kind), get_args(kind)
    if origin is UnionType:
        return _decode(next(item for item in args if item is not type(None)), value)
    if origin is list:
        return [_decode(args[0], item) for item in value]
    if is_dataclass(kind):
        hints = _model_hints(kind)
        return kind(**{key: _decode(hints[key], item) for key, item in value.items()})
    return value


@dataclass
class Model:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, indent=2) + "\n"


@dataclass
class Anchor(Model):
    system: str
    code: str
    title: str
    fit: Literal["close", "partial", "exact"]


@dataclass
class Signal(Model):
    source: str
    source_kind: SourceKind
    family: Family
    timing: Timing
    reading: str
    direction: Direction
    as_of: str | None = None


@dataclass
class Foundation(Model):
    median_pay: str
    pay_note: str
    employment: str
    employment_note: str
    outlook_10y: str
    outlook_note: str
    core_tasks: list[str]
    sources: list[Signal]


@dataclass
class EvidenceCounts(Model):
    postings: int
    employers: int
    window: str
    timing: Timing


@dataclass
class DemandWindow(Model):
    id: str
    compared_to: str | None
    postings: int
    employers: int
    max_employer_share: float
    timing: Timing
    window_length: Literal["quarterly", "monthly"] = "quarterly"


@dataclass
class Expectation(Model):
    id: str
    label: str
    introduced_at: int | Literal["earlier"] | None
    basis: Literal["advertised_demand"]
    cite: str
    timing: Timing


@dataclass
class Level(Model):
    id: str
    level: int
    name: str
    typical_titles: list[str]
    years_wording: str
    scope: str
    owns: str
    step_up: str
    evidence: EvidenceCounts
    expectations: list[Expectation]


@dataclass
class LinkedRole(Model):
    from_level: int
    role: str
    relation: Literal["people_management_track"]
    note: str


@dataclass
class Ladder(Model):
    scale: Literal["shared L1-L5"]
    track: Literal["individual_contributor"]
    linked_roles: list[LinkedRole]
    levels: list[Level]


@dataclass
class Measurement(Model):
    """Skill share at its stated levels; employer spread is for the current window.

    comparison_group identifies a comparable sampling method. A changed group or
    level scope cannot support a trend. Shares are fractions, not percentages.
    The explicit "unassigned" stratum retains observations without a level.
    """

    window: str
    share: float
    employers: int
    max_employer_share: float
    comparison_group: str
    levels: list[int | Literal["unassigned"]]
    timing: Timing


@dataclass
class Forecast(Model):
    id: str
    claim: str
    probability: float
    base_rate: float
    horizon_quarters: Literal[4]
    created: str
    resolves: str
    scored_against: str


@dataclass
class Skill(Model):
    id: str
    short_label: str
    label: str
    status: SkillStatus
    first_level: int | None
    share_by_level: list[float | None]
    source_links: list[str]
    windows: list[Measurement]
    evidence: list[Signal]
    forecast: Forecast | None
    disputed: str | None


@dataclass
class AskedTogether(Model):
    a: str
    b: str
    lift: float
    postings: int
    employers: int
    timing: Timing


@dataclass
class BuildsOn(Model):
    a: str
    b: str


@dataclass
class TopLevelAdds(Model):
    top_level: int
    core_level: int
    skills: list[str]
    long_run_bar: Signal


@dataclass
class SkillMap(Model):
    skills: list[Skill]
    asked_together: list[AskedTogether]
    builds_on: list[BuildsOn]
    top_level_adds: TopLevelAdds


@dataclass
class Certification(Model):
    name: str
    issuer: str
    asked_as: Literal["required", "preferred", "common"]
    posting_share: float
    source: str
    timing: Timing


@dataclass
class SkillShare(Model):
    skill: str
    share: float
    timing: Timing


@dataclass
class SectorProfile(Model):
    share_of_occupation_jobs: str
    median_pay: str
    how_it_differs: str
    distinct_duties: list[str]
    certifications: list[Certification]
    domain_knowledge: list[str]
    skill_shares: list[SkillShare]
    sources: list[Signal]


@dataclass
class Industry(Model):
    naics: str
    label: str
    naics_title: str
    sub_industries: str
    evidence: EvidenceCounts
    profile: SectorProfile | None
    reason: str | None


@dataclass
class PostingWindow(Model):
    id: str
    mentions: int
    postings: int
    employers: int
    timing: Timing


@dataclass
class Observation(Model):
    """Prototype posting counts, without promoting its legacy shift-state labels."""

    id: str
    label: str
    levels: list[int]
    windows: list[PostingWindow]
    note: str


@dataclass
class RoleRecord(Model):
    schema: Literal["skills-vector.role/2"]
    id: str
    label: str
    status: Literal["established", "emerging"]
    publication: Literal["standard", "experimental"]
    data_kind: Literal["fixture", "research"]
    release_id: str
    aliases: list[str]
    anchors: list[Anchor]
    foundation: Foundation
    demand_window: DemandWindow
    ladder: Ladder
    skill_map: SkillMap
    industries: list[Industry]
    observations: list[Observation]
    limits: list[str]

    def __post_init__(self) -> None:
        validate_role(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Self:
        _validate_structure(payload)
        return _decode(cls, payload)

    @classmethod
    def from_json(cls, payload: str) -> Self:
        try:
            return cls.from_dict(json.loads(payload))
        except (TypeError, json.JSONDecodeError) as exc:
            raise RoleValidationError(str(exc)) from exc


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RoleValidationError(message)


def _period(value: str) -> int:
    year, period = value.split("-")
    month = (int(period[1:]) - 1) * 3 if period.startswith("Q") else int(period) - 1
    return int(year) * 12 + month


def _validate_measurements(windows: list[Measurement]) -> None:
    ids = [window.window for window in windows]
    _require(len(ids) == len(set(ids)), "duplicate measurement windows")
    for window in windows:
        _require(bool(re.fullmatch(r"\d{4}-(?:Q[1-4]|0[1-9]|1[0-2])", window.window)), "invalid measurement window")
        _require(0 <= window.share <= 1, "measurement share outside 0–1")
        _require(type(window.employers) is int and window.employers >= 0, "invalid measurement employers")
        _require(0 <= window.max_employer_share <= 1, "invalid employer share")
        _require(bool(window.comparison_group.strip()), "missing comparison group")
        _require(bool(window.levels) and len(set(window.levels)) == len(window.levels)
                 and all(level == "unassigned" or (type(level) is int and 1 <= level <= 5) for level in window.levels), "invalid measurement levels")
        _require(window.timing == "coincident", "window shares must be coincident")
        _require(window.employers > 0 or window.share == 0, "positive share without employers")
    _require(ids == sorted(ids, key=_period), "measurement windows must be chronological")


def derive_status(windows: list[Measurement], evidence: list[Signal]) -> SkillStatus:
    """Pure status rule: fading precedes core, then rising, then early.

    The latest window supplies the employer/spread floor. Only windows with the
    same sampling group and levels are comparable. Core needs the current share
    plus another comparable window at 25% and ten current employers; it does not
    need non-posting confirmation. Fading uses rising's employer/spread floor.
    """
    _validate_measurements(windows)
    if not windows:
        return "early"
    current = windows[-1]
    comparable = [window for window in windows[:-1]
                  if window.comparison_group == current.comparison_group
                  and set(window.levels) == set(current.levels)
                  and ("Q" in window.window) == ("Q" in current.window)]
    if not comparable:
        return "early"
    delta = Decimal(str(current.share)) - Decimal(str(comparable[-1].share))
    spread = current.employers >= 8 and current.max_employer_share <= 0.2
    if spread and delta <= Decimal("-0.05"):
        return "fading"
    if current.share >= 0.25 and current.employers >= 10 and any(window.share >= 0.25 for window in comparable):
        return "core"
    if delta >= Decimal("0.05") and spread and any(signal.family in ("O", "M") and signal.direction == "up" for signal in evidence):
        return "rising"
    return "early"


def edge_qualifies(edge: AskedTogether) -> bool:
    return edge.lift >= 1.5 and edge.postings >= 25 and edge.employers >= 8


def sector_qualifies(evidence: EvidenceCounts) -> bool:
    return evidence.postings >= 30 and evidence.employers >= 8


def _validate_structure(payload: Any) -> None:
    error = next(_validator().iter_errors(payload), None)
    if error is not None:
        path = ".".join(str(part) for part in error.path) or "record"
        raise RoleValidationError(f"{path}: {error.message}")
    # JSON Schema's numeric comparisons alone do not exclude Python NaN/Infinity.
    try:
        json.dumps(payload, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise RoleValidationError(str(exc)) from exc


def validate_role(record: RoleRecord) -> None:
    _validate_structure(record.to_dict())
    expected_publication = "experimental" if record.status == "emerging" else "standard"
    _require(record.publication == expected_publication, f"{record.status} roles require {expected_publication} publication")
    demand = record.demand_window
    _require(("Q" in demand.id) == (demand.window_length == "quarterly"), "demand window cadence mismatch")
    if demand.compared_to is not None:
        _require(_period(demand.compared_to) < _period(demand.id), "compared window must precede current window")
        _require(("Q" in demand.compared_to) == ("Q" in demand.id), "compared window cadence mismatch")
    levels = [level.level for level in record.ladder.levels]
    _require(levels == list(range(1, len(levels) + 1)), "ladder must use consecutive shared levels from L1")
    _require(len({level.id for level in record.ladder.levels}) == len(levels), "duplicate level ids")
    for link in record.ladder.linked_roles:
        _require(link.from_level in levels, "linked role references unknown level")
    skills = record.skill_map.skills
    ids = {skill.id for skill in skills}
    _require(len(ids) == len(skills), "duplicate skill ids")
    for skill in skills:
        _require((skill.first_level is None or skill.first_level in levels) and len(skill.share_by_level) == len(levels), "invalid skill level coverage")
        if skill.first_level is None:
            _require(not any(share is not None and share > 0 for share in skill.share_by_level), "unknown first level has observed positive coverage")
        if record.data_kind == "research":
            for level, share in zip(record.ladder.levels, skill.share_by_level, strict=True):
                _require((share is None) == (level.evidence.postings == 0), "research share must preserve missing assigned denominators")
            if skill.first_level is not None:
                first = skill.share_by_level[skill.first_level - 1]
                _require(first is not None and first > 0, "research first level needs an observed mention")
        for window in skill.windows:
            _require(set(window.levels) <= set(levels) | {"unassigned"}, "measurement references unknown level")
        _require(bool(skill.windows) and skill.windows[-1].window == record.demand_window.id, "skill must measure the current window")
        expected = derive_status(skill.windows, skill.evidence)
        _require(skill.status == expected, f"{skill.id}: status must be {expected}")
        _require(skill.status == "core" or skill.forecast is not None, f"{skill.id}: non-core skill needs a forecast")
        if skill.forecast is not None:
            forecast = skill.forecast
            _require(_period(forecast.resolves) - _period(forecast.created) == 12, "forecast must resolve four quarters after creation")
            _require(forecast.created == record.demand_window.id, "forecast must be dated to the demand window")
    pairs: set[tuple[str, str]] = set()
    for edge in record.skill_map.asked_together:
        _require(edge_qualifies(edge), "asked-together edge below threshold")
        _require(edge.a in ids and edge.b in ids and edge.a != edge.b, "invalid asked-together endpoints")
        pair = tuple(sorted((edge.a, edge.b)))
        _require(pair not in pairs, "duplicate asked-together edge")
        pairs.add(pair)
    for edge in record.skill_map.builds_on:
        _require(edge.a in ids and edge.b in ids and edge.a != edge.b, "invalid builds-on endpoints")
    top = record.skill_map.top_level_adds
    _require(top.top_level == max(levels) and top.core_level in levels and top.core_level < top.top_level,
             "top-level adds must compare the highest level with a lower core level")
    expected_top = {skill.id for skill in skills
                    if skill.share_by_level[top.top_level - 1] is not None
                    and skill.share_by_level[top.core_level - 1] is not None
                    and skill.share_by_level[top.top_level - 1] >= 40
                    and skill.share_by_level[top.top_level - 1] - skill.share_by_level[top.core_level - 1] >= 20}
    _require(set(top.skills) == expected_top and len(top.skills) == len(expected_top), "incorrect top-level posting comparison")
    _require(top.long_run_bar.timing == "lagging", "O*NET long-run bar must be lagging")
    _require(len({industry.naics for industry in record.industries}) == len(record.industries), "duplicate NAICS sectors")
    for industry in record.industries:
        if not sector_qualifies(industry.evidence):
            _require(industry.profile is None and bool(industry.reason and industry.reason.strip()), "thin sector needs profile null and a reason")
        if industry.profile is None:
            _require(bool(industry.reason and industry.reason.strip()), "absent sector profile needs a reason")
        else:
            _require(industry.reason is None, "profile must not carry an absence reason")
            shares = [item.skill for item in industry.profile.skill_shares]
            _require(set(shares) <= ids and len(shares) == len(set(shares)), "invalid industry skill shares")
    for counts in _walk(record, EvidenceCounts):
        _require(counts.employers <= counts.postings, "employers exceed postings")
        _require(counts.window == record.demand_window.id, "evidence must match demand window")
    _require(record.demand_window.employers <= record.demand_window.postings, "demand employers exceed postings")
    for window in _walk(record, PostingWindow):
        _require(window.mentions <= window.postings and window.employers <= window.mentions, "invalid observation counts")
    for signal in _walk(record, Signal):
        timing, family = SOURCE_RULES[signal.source_kind]
        _require(signal.timing == timing, f"{signal.source_kind} timing must be {timing}")
        _require(signal.family == family, f"{signal.source_kind} family must be {family}")
    for level in record.ladder.levels:
        for expectation in level.expectations:
            _require(expectation.introduced_at is None or expectation.introduced_at == "earlier" or expectation.introduced_at <= level.level, "expectation introduced after its level")
            _require(expectation.cite == f"/v1/expectations/{expectation.id}@{record.release_id}", "expectation citation must be release-pinned")


def _walk(value: Any, kind: type) -> list[Any]:
    if isinstance(value, kind):
        return [value]
    if is_dataclass(value):
        return [item for field in fields(value) for item in _walk(getattr(value, field.name), kind)]
    if isinstance(value, list):
        return [item for child in value for item in _walk(child, kind)]
    return []


@dataclass
class RoleCatalogStore:
    """Read-only sibling of CatalogStore. Fixture access is explicit, never a release fallback.

    A root contains one role/2 JSON document per role. No method writes files or
    consults preview/release/current.json. Validation is cached by path/mtime;
    public reads return fresh dictionaries, never the cached models.
    """

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self._cache: dict[tuple[Path, int], RoleRecord] = {}

    @classmethod
    def fixtures(cls) -> Self:
        return cls(FIXTURE_ROOT)

    def list_roles(self, release_id: str | None = None) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self._records(release_id)]

    def role(self, role: str, release_id: str | None = None) -> dict[str, Any] | None:
        return next((record.to_dict() for record in self._records(release_id)
                     if role in (record.id, record.id.removeprefix("role."))), None)

    def _records(self, release_id: str | None) -> list[RoleRecord]:
        records = []
        cache = {}
        for path in sorted(self.root.glob("*.json")):
            path = path.resolve()
            key = (path, path.stat().st_mtime_ns)
            record = self._cache.get(key)
            if record is None:
                record = RoleRecord.from_json(path.read_text(encoding="utf-8"))
            cache[key] = record
            records.append(record)
        _require(len({(record.id, record.release_id) for record in records}) == len(records), "duplicate role in catalog release")
        if self.root.resolve() == FIXTURE_ROOT.resolve():
            _require(all(record.data_kind == "fixture" for record in records), "fixture catalog contains non-fixture data")
        self._cache = cache
        return [record for record in records if release_id is None or record.release_id == release_id]
