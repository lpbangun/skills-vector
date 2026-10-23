"""Offline contract, rule boundaries, prototype fidelity and read-only catalog tests."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from skills_vector.market.role_atlas import (
    CONTRACT_ROOT,
    FIXTURE_ROOT,
    SCHEMA_PATH,
    SOURCE_RULES,
    AskedTogether,
    DemandWindow,
    EvidenceCounts,
    Measurement,
    RoleCatalogStore,
    RoleRecord,
    RoleValidationError,
    Signal,
    derive_status,
    edge_qualifies,
    sector_qualifies,
    validate_role,
    _validator,
)

ROOT = Path(__file__).resolve().parents[1]


def measurement(share: float, *, window: str = "2026-Q3", employers: int = 8,
                concentration: float = 0.2, group: str = "sample", levels: list[int] | None = None) -> Measurement:
    return Measurement(window, share, employers, concentration, group, levels or [1, 2], "coincident")


def signals(family: str = "M", direction: str = "up") -> list[Signal]:
    kind = {"M": "hiring_lab", "O": "onet_emerging_tasks", "P": "postings", "context": "bls_projections"}[family]
    timing, _ = SOURCE_RULES[kind]
    return [Signal("Job postings", "postings", "P", "coincident", "Sample share", "up"),
            Signal("Independent source", kind, family, timing, "Independent direction", direction)]


class StatusRuleTests(unittest.TestCase):
    def test_core_exactly_25_percent_and_ten_employers(self) -> None:
        previous = measurement(0.25, window="2026-Q2", employers=10)
        current = measurement(0.25, employers=10)
        self.assertEqual(derive_status([previous, current], signals("P")), "core")
        self.assertEqual(derive_status([previous, replace(current, share=0.2499)], signals()), "early")
        self.assertEqual(derive_status([previous, replace(current, employers=9)], signals()), "early")
        self.assertEqual(derive_status([replace(previous, share=0.2499), current], signals()), "early")
        self.assertEqual(derive_status([current], signals()), "early")
        self.assertEqual(derive_status([], signals()), "early")
        # Core has no concentration ceiling or non-posting-family requirement.
        self.assertEqual(derive_status([previous, replace(current, max_employer_share=0.21)], []), "core")

    def test_rising_exactly_five_points_not_float_rounding(self) -> None:
        previous = measurement(0.10, window="2026-Q2", employers=6)
        current = measurement(0.15)
        self.assertEqual(derive_status([previous, current], signals()), "rising")
        self.assertEqual(derive_status([previous, replace(current, share=0.14999)], signals()), "early")
        self.assertEqual(derive_status([previous, replace(current, employers=7)], signals()), "early")
        self.assertEqual(derive_status([previous, replace(current, max_employer_share=0.21)], signals()), "early")
        self.assertEqual(derive_status([previous, current], signals("O")), "rising")
        self.assertEqual(derive_status([previous, current], signals("P")), "early")
        self.assertEqual(derive_status([previous, current], signals("context")), "early")
        self.assertEqual(derive_status([previous, current], signals(direction="presence")), "early")
        self.assertEqual(derive_status([current], signals()), "early")

    def test_rising_requires_an_upward_non_posting_signal(self) -> None:
        windows = [measurement(0.10, window="2026-Q2"), measurement(0.15)]
        for direction in ("presence", "down", "flat"):
            for family in ("O", "M"):
                with self.subTest(direction=direction, family=family):
                    self.assertEqual(derive_status(windows, signals(family, direction)), "early")
        self.assertEqual(derive_status(windows, signals("M", "up")), "rising")
        self.assertEqual(derive_status(windows, signals("O", "up")), "rising")

    def test_fading_is_not_blocked_by_presence_in_a_lagging_map(self) -> None:
        windows = [measurement(0.20, window="2026-Q2"), measurement(0.15)]
        official = Signal("O*NET importance", "onet_ratings", "O", "lagging", "Still listed", "presence")
        self.assertEqual(derive_status(windows, [official]), "fading")

    def test_fading_exactly_five_points_same_spread_floor(self) -> None:
        previous = measurement(0.30, window="2026-Q2")
        current = measurement(0.25)
        self.assertEqual(derive_status([previous, current], []), "fading")
        self.assertEqual(derive_status([previous, replace(current, share=0.25001)], []), "early")
        self.assertEqual(derive_status([previous, replace(current, employers=7)], []), "early")
        self.assertEqual(derive_status([previous, replace(current, max_employer_share=0.21)], []), "early")
        # Recheck core every window: a drop wins even if both shares exceed 25%.
        self.assertEqual(derive_status([previous, replace(current, employers=10)], []), "fading")

    def test_core_is_not_downgraded_for_lacking_external_confirmation(self) -> None:
        previous = measurement(0.25, window="2026-Q2", employers=10)
        current = measurement(0.30, employers=10)
        self.assertEqual(derive_status([previous, current], signals("P")), "core")

    def test_only_comparable_windows_support_status(self) -> None:
        previous = measurement(0.10, window="2026-Q2")
        current = measurement(0.15)
        for changed in (replace(previous, comparison_group="other"), replace(previous, levels=[3]),
                        replace(previous, window="2026-06")):
            with self.subTest(changed=changed):
                self.assertEqual(derive_status([changed, current], signals()), "early")
        self.assertEqual(derive_status([previous, replace(current, levels=[2, 1])], signals()), "rising")
        windows = [measurement(0.05, window="2026-Q1"), previous, current]
        before = copy.deepcopy(windows)
        self.assertEqual(derive_status(windows, signals()), "rising")
        self.assertEqual(windows, before)
        # The latest comparable pair, not the most convenient historical change.
        self.assertEqual(derive_status([windows[0], replace(previous, share=0.15), current], signals()), "early")

    def test_monthly_windows_supported_without_changing_forecast_horizon(self) -> None:
        previous = measurement(0.10, window="2026-06")
        current = measurement(0.15, window="2026-07")
        self.assertEqual(derive_status([previous, current], signals()), "rising")
        self.assertEqual(DemandWindow("2026-Q3", None, 30, 8, 0.2, "coincident").window_length, "quarterly")

    def test_invalid_measurements_rejected(self) -> None:
        current = measurement(0.15)
        invalid = [replace(current, share=-0.01), replace(current, share=1.01),
                   replace(current, max_employer_share=1.01), replace(current, employers=-1),
                   replace(current, employers=0), replace(current, comparison_group=" "),
                   replace(current, window="bad"), replace(current, levels=[0]),
                   replace(current, levels=[]), replace(current, levels=[6]),
                   replace(current, levels=[1, 1]), replace(current, timing="leading")]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(RoleValidationError):
                derive_status([item], signals())
        for windows in ([current, current], [current, replace(current, window="2026-Q2")]):
            with self.assertRaises(RoleValidationError):
                derive_status(windows, signals())


class RoleContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.validator = Draft202012Validator(cls.schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
        cls.catalog = RoleCatalogStore.fixtures()
        cls.payload = cls.catalog.role("account-executive")

    def skill(self, payload: dict, key: str = "aires") -> dict:
        return next(item for item in payload["skill_map"]["skills"] if item["id"] == "skill." + key)

    def reject(self, payload: dict, message: str | None = None) -> None:
        with self.assertRaises(RoleValidationError) as raised:
            RoleRecord.from_dict(payload)
        if message:
            self.assertIn(message, str(raised.exception))

    def test_schema_validator_is_loaded_and_checked_once_per_process(self) -> None:
        _validator.cache_clear()
        with patch.object(Draft202012Validator, "check_schema", wraps=Draft202012Validator.check_schema) as check:
            first = _validator()
            self.assertIs(_validator(), first)
            self.assertEqual(check.call_count, 1)
        self.assertEqual(_validator.cache_info().hits, 1)

    def test_all_models_round_trip_through_real_json_schema(self) -> None:
        self.validator.check_schema(self.schema)
        for payload in self.catalog.list_roles():
            with self.subTest(role=payload["id"]):
                model = RoleRecord.from_dict(payload)
                serialized = json.loads(model.to_json())
                self.validator.validate(serialized)
                rebuilt = RoleRecord.from_dict(serialized)
                self.assertEqual(rebuilt, model)
                self.assertEqual(rebuilt.to_dict(), payload)
                self.assertIsInstance(rebuilt.demand_window, DemandWindow)
                self.assertIsInstance(rebuilt.skill_map.skills[0].windows[0], Measurement)
                self.assertIsInstance(rebuilt.foundation.sources[0], Signal)
                self.assertEqual(rebuilt.schema, "skills-vector.role/2")

    def test_optional_source_as_of_round_trip(self) -> None:
        for as_of in ("2021", "2020-02-29", None):
            with self.subTest(as_of=as_of):
                payload = copy.deepcopy(self.payload)
                payload["foundation"]["sources"][0]["as_of"] = as_of
                record = RoleRecord.from_dict(payload)
                self.assertEqual(record.foundation.sources[0].as_of, as_of)
                serialized = json.loads(record.to_json())
                self.validator.validate(serialized)
                self.assertEqual(serialized, payload)
                self.assertEqual(RoleRecord.from_dict(serialized), record)
        payload = copy.deepcopy(self.payload)
        del payload["foundation"]["sources"][0]["as_of"]
        self.assertIsNone(RoleRecord.from_dict(payload).foundation.sources[0].as_of)

    def test_invalid_source_as_of_is_rejected(self) -> None:
        for as_of in ("", "rated 2021", "2021\n", "2021-2-03", "2021-02-29",
                      "2020-02-30", "2021-04-31", "2021-13-01", "2021-01-01T00:00:00Z",
                      2021, True, {}):
            with self.subTest(as_of=as_of):
                payload = copy.deepcopy(self.payload)
                payload["foundation"]["sources"][0]["as_of"] = as_of
                self.assertFalse(self.validator.is_valid(payload))
                self.reject(payload, "as_of")
        for as_of in ("rated 2021", "2021\n", "2021-13-01", "2021-01-01T00:00:00Z"):
            payload["foundation"]["sources"][0]["as_of"] = as_of
            self.assertFalse(Draft202012Validator(self.schema).is_valid(payload))

    def test_non_core_forecast_required_but_core_can_have_none(self) -> None:
        self.assertIsNone(self.skill(self.payload, "prosp")["forecast"])
        self.assertEqual(self.skill(self.payload)["forecast"]["probability"], 0.72)
        for status, key in (("rising", "aires"), ("early", "consumption"), ("fading", "coldcall")):
            payload = copy.deepcopy(self.payload)
            skill = self.skill(payload, key)
            self.assertEqual(skill["status"], status)
            skill["forecast"] = None
            self.assertFalse(self.validator.is_valid(payload))
            self.reject(payload)

    def test_forecast_fields_ranges_and_four_quarter_resolve(self) -> None:
        forecast = self.skill(self.payload)["forecast"]
        self.assertEqual((forecast["horizon_quarters"], forecast["created"], forecast["resolves"], forecast["base_rate"]),
                         (4, "2026-Q3", "2027-Q3", 0.62))
        for field, value in (("horizon_quarters", 3), ("horizon_quarters", 5), ("probability", -0.01),
                             ("probability", 1.01), ("base_rate", None), ("base_rate", -0.01), ("base_rate", 1.01),
                             ("claim", ""), ("scored_against", ""), ("created", "not-a-date"), ("resolves", "2027-Q2")):
            with self.subTest(field=field, value=value):
                payload = copy.deepcopy(self.payload)
                self.skill(payload)["forecast"][field] = value
                self.reject(payload)
        for field in ("base_rate", "created", "resolves", "claim", "scored_against"):
            payload = copy.deepcopy(self.payload)
            del self.skill(payload)["forecast"][field]
            self.reject(payload)
        for boundary in (0, 1):
            payload = copy.deepcopy(self.payload)
            self.skill(payload)["forecast"]["probability"] = boundary
            self.skill(payload)["forecast"]["base_rate"] = boundary
            model = RoleRecord.from_dict(payload)
            self.assertEqual(next(skill for skill in model.skill_map.skills if skill.id == "skill.aires").forecast.probability, boundary)
        for value in (float("nan"), float("inf"), float("-inf")):
            payload = copy.deepcopy(self.payload)
            self.skill(payload)["forecast"]["probability"] = value
            self.reject(payload)

    def test_forecast_bases_require_real_numbers_or_explicit_absence(self) -> None:
        for basis in ("fixture", "backtest", "none"):
            payload = copy.deepcopy(self.payload)
            forecast = self.skill(payload)["forecast"]
            forecast["basis"] = basis
            if basis == "none":
                forecast.update(probability=None, base_rate=None, reason="One window; no historical base rate.")
            rebuilt = RoleRecord.from_dict(payload)
            self.assertEqual(self.skill(rebuilt.to_dict())["forecast"], forecast)
            self.assertEqual(RoleRecord.from_json(rebuilt.to_json()), rebuilt)
            for field in ("probability", "base_rate"):
                bad = copy.deepcopy(payload)
                self.skill(bad)["forecast"][field] = 0.2 if basis == "none" else None
                self.reject(bad)
        for reason in (None, "", " "):
            payload = copy.deepcopy(self.payload)
            self.skill(payload)["forecast"].update(basis="none", probability=None, base_rate=None, reason=reason)
            self.reject(payload)
        for field in ("basis", "reason"):
            payload = copy.deepcopy(self.payload)
            self.skill(payload)["forecast"].update(basis="none", probability=None, base_rate=None, reason="No archive")
            del self.skill(payload)["forecast"][field]
            self.reject(payload)
        payload = copy.deepcopy(self.payload)
        self.skill(payload)["forecast"]["basis"] = "sample_fraction"
        self.reject(payload)

    def test_status_cannot_be_asserted_against_the_rules(self) -> None:
        for key in ("prosp", "aires", "consumption", "coldcall"):
            payload = copy.deepcopy(self.payload)
            skill = self.skill(payload, key)
            expected = skill["status"]
            skill["status"] = "early" if expected != "early" else "rising"
            if skill["forecast"] is None:
                skill["forecast"] = copy.deepcopy(self.skill(payload)["forecast"])
            self.reject(payload, f"status must be {expected}")
        payload = copy.deepcopy(self.payload)
        for evidence in self.skill(payload)["evidence"]:
            if evidence["family"] in ("O", "M"):
                evidence["direction"] = "presence"
        self.reject(payload, "status must be early")

    def test_disputed_is_a_flag_not_a_fifth_status(self) -> None:
        fde = self.catalog.role("forward-deployed-engineer")
        presales = self.skill(fde, "presales")
        self.assertEqual(presales["status"], "core")
        self.assertIn("pre-sales", presales["disputed"])
        presales["status"] = "disputed"
        self.reject(fde)

    def test_edge_floor_all_exact_boundaries(self) -> None:
        edge = AskedTogether("skill.prosp", "skill.crm", 1.5, 25, 8, "coincident")
        self.assertTrue(edge_qualifies(edge))
        payload = copy.deepcopy(self.payload)
        payload["skill_map"]["asked_together"] = [edge.to_dict()]
        self.assertEqual(RoleRecord.from_dict(payload).skill_map.asked_together[0].postings, 25)
        for bad in (replace(edge, lift=1.499), replace(edge, postings=24), replace(edge, employers=7)):
            with self.subTest(edge=bad):
                self.assertFalse(edge_qualifies(bad))
                payload["skill_map"]["asked_together"] = [bad.to_dict()]
                self.assertFalse(self.validator.is_valid(payload))
                self.reject(payload)
        for bad in (replace(edge, a="skill.absent"), replace(edge, b=edge.a)):
            payload["skill_map"]["asked_together"] = [bad.to_dict()]
            self.reject(payload, "endpoints")
        payload["skill_map"]["asked_together"] = [edge.to_dict(), replace(edge, a=edge.b, b=edge.a).to_dict()]
        self.reject(payload, "duplicate")

    def test_sector_floor_and_visible_absence(self) -> None:
        payload = copy.deepcopy(self.payload)
        sector = payload["industries"][0]
        sector["evidence"].update(postings=30, employers=8)
        self.assertTrue(sector_qualifies(EvidenceCounts(**sector["evidence"])))
        self.assertEqual(RoleRecord.from_dict(payload).industries[0].evidence.postings, 30)
        for field, value in (("postings", 29), ("employers", 7)):
            thin = copy.deepcopy(payload)
            thin["industries"][0]["evidence"][field] = value
            self.assertFalse(sector_qualifies(EvidenceCounts(**thin["industries"][0]["evidence"])))
            self.assertFalse(self.validator.is_valid(thin))
            self.reject(thin)
            thin["industries"][0].update(profile=None, reason="below profile floor")
            self.assertIsNone(RoleRecord.from_dict(thin).industries[0].profile)
            for reason in (None, "", " "):
                thin["industries"][0]["reason"] = reason
                self.reject(thin)
        fde = RoleRecord.from_dict(self.catalog.role("forward-deployed-engineer"))
        self.assertEqual((fde.industries[-1].evidence.postings, fde.industries[-1].evidence.employers), (12, 5))
        self.assertIsNone(fde.industries[-1].profile)
        self.assertEqual(fde.industries[-1].reason, "below 30 postings / 8 employers")

    def test_experimental_publication_depends_on_role_status_not_anchor_fit(self) -> None:
        self.assertEqual(self.payload["publication"], "standard")
        self.assertEqual(self.payload["anchors"][0]["fit"], "partial")
        for role in self.catalog.list_roles():
            expected = "experimental" if role["status"] == "emerging" else "standard"
            self.assertEqual(RoleRecord.from_dict(role).publication, expected)
            for fit in ("partial", "close", "exact"):
                with self.subTest(role=role["id"], fit=fit):
                    payload = copy.deepcopy(role)
                    payload["anchors"][0]["fit"] = fit
                    self.assertEqual(RoleRecord.from_dict(payload).publication, expected)
                    payload["publication"] = "standard" if expected == "experimental" else "experimental"
                    self.assertFalse(self.validator.is_valid(payload))
                    self.reject(payload)

    def test_every_signal_requires_valid_timing_including_nested_counts(self) -> None:
        def signals_with_timing(value: object, path: tuple = ()) -> list[tuple]:
            if isinstance(value, dict):
                found = [path] if "timing" in value else []
                return found + [item for key, child in value.items() for item in signals_with_timing(child, path + (key,))]
            if isinstance(value, list):
                return [item for index, child in enumerate(value) for item in signals_with_timing(child, path + (index,))]
            return []

        paths = signals_with_timing(self.payload)
        self.assertGreater(len(paths), 100)
        for path in paths:
            for invalid in (None, "future"):
                with self.subTest(path=path, invalid=invalid):
                    payload = copy.deepcopy(self.payload)
                    target = payload
                    for part in path:
                        target = target[part]
                    if invalid is None:
                        del target["timing"]
                    else:
                        target["timing"] = invalid
                    self.reject(payload)

    def test_source_kind_requires_canonical_timing_and_family_for_every_kind(self) -> None:
        expected_rules = {
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
        self.assertEqual(SOURCE_RULES, expected_rules)
        self.assertEqual(set(self.schema["$defs"]["Signal"]["properties"]["source_kind"]["enum"]), set(expected_rules))
        for kind, (timing, family) in expected_rules.items():
            with self.subTest(kind=kind):
                payload = copy.deepcopy(self.payload)
                source = Signal("Arbitrary display name", kind, family, timing, "A source reading", "presence")
                payload["foundation"]["sources"][0] = source.to_dict()
                rebuilt = RoleRecord.from_dict(payload).foundation.sources[0]
                self.assertEqual((rebuilt.source_kind, rebuilt.timing, rebuilt.family), (kind, timing, family))
                payload["foundation"]["sources"][0]["timing"] = "lagging" if timing != "lagging" else "coincident"
                self.reject(payload, f"{kind} timing must be {timing}")
                payload["foundation"]["sources"][0] = source.to_dict()
                payload["foundation"]["sources"][0]["family"] = "M" if family != "M" else "O"
                self.reject(payload, f"{kind} family must be {family}")
        payload = copy.deepcopy(self.payload)
        payload["foundation"]["sources"][0].update(source="O*NET importance", timing="coincident")
        self.reject(payload, "onet_ratings timing must be lagging")

    def test_signal_kind_and_direction_are_closed_required_fields(self) -> None:
        for field, invalid in (("source_kind", "other"), ("direction", "agree")):
            payload = copy.deepcopy(self.payload)
            payload["foundation"]["sources"][0][field] = invalid
            self.reject(payload)
            del payload["foundation"]["sources"][0][field]
            self.reject(payload)
        payload = copy.deepcopy(self.payload)
        payload["foundation"]["sources"][0]["agrees"] = True
        self.reject(payload)

    def test_rising_record_requires_up_direction_and_fading_keeps_map_presence(self) -> None:
        for direction in ("presence", "down", "flat", "up"):
            payload = copy.deepcopy(self.payload)
            for signal in self.skill(payload)["evidence"]:
                if signal["family"] in ("O", "M"):
                    signal["direction"] = direction
            if direction == "up":
                self.assertEqual(self.skill(RoleRecord.from_dict(payload).to_dict())["status"], "rising")
            else:
                self.reject(payload, "status must be early")
        skill = self.skill(self.payload, "coldcall")
        self.assertEqual(skill["status"], "fading")
        self.assertEqual(next(signal for signal in skill["evidence"] if signal["source_kind"] == "onet_ratings")["direction"], "presence")

    def test_unknown_or_missing_fields_rejected_not_silently_lost(self) -> None:
        for target in ("root", "forecast", "evidence"):
            payload = copy.deepcopy(self.payload)
            node = payload if target == "root" else self.skill(payload)["forecast"] if target == "forecast" else self.skill(payload)["evidence"][0]
            node["unrecognized"] = "must not disappear"
            self.reject(payload)
        for key in self.payload:
            payload = copy.deepcopy(self.payload)
            del payload[key]
            self.reject(payload)
        for invalid in ([], None, "string", 3):
            self.reject(invalid)
        with self.assertRaises(RoleValidationError):
            RoleRecord.from_json("{")

    def test_reference_integrity_and_top_level_comparison(self) -> None:
        for mutate in (
            lambda p: p["skill_map"]["skills"].append(copy.deepcopy(p["skill_map"]["skills"][0])),
            lambda p: p["ladder"]["levels"][0].update(level=2),
            lambda p: p["ladder"]["linked_roles"][0].update(from_level=5 + 1),
            lambda p: p["skill_map"]["skills"][0].update(share_by_level=[30]),
            lambda p: p["skill_map"]["top_level_adds"].update(core_level=5),
            lambda p: p["skill_map"]["top_level_adds"].update(skills=[]),
            lambda p: p["skill_map"]["builds_on"][0].update(a="skill.missing"),
            lambda p: p["industries"][0]["profile"]["skill_shares"][0].update(skill="skill.missing"),
            lambda p: p["ladder"]["levels"][0]["evidence"].update(employers=999),
            lambda p: p["ladder"]["levels"][0]["evidence"].update(window="2026-Q2"),
            lambda p: p["ladder"]["levels"][0]["expectations"][0].update(cite="/unpinned"),
            lambda p: p["demand_window"].update(compared_to="2026-Q4"),
            lambda p: p["demand_window"].update(window_length="monthly"),
        ):
            payload = copy.deepcopy(self.payload)
            mutate(payload)
            self.reject(payload)
        top = RoleRecord.from_dict(self.payload).skill_map.top_level_adds
        self.assertEqual((top.top_level, top.core_level), (5, 3))
        self.assertIn("skill.dealstruct", top.skills)
        self.assertEqual(top.long_run_bar.timing, "lagging")

    def test_additional_cross_field_rejections(self) -> None:
        for mutate in (
            lambda p: p["ladder"]["levels"][1].update(id=p["ladder"]["levels"][0]["id"]),
            lambda p: p["industries"].append(copy.deepcopy(p["industries"][0])),
            lambda p: p["industries"][0]["profile"]["skill_shares"].append(copy.deepcopy(p["industries"][0]["profile"]["skill_shares"][0])),
            lambda p: p["industries"][0].update(reason="profile is not absent"),
            lambda p: p["observations"][0]["windows"][0].update(mentions=999),
            lambda p: p["observations"][0]["windows"][0].update(employers=999),
            lambda p: p["ladder"]["levels"][0]["expectations"][0].update(introduced_at=2),
            lambda p: p["demand_window"].update(postings=1),
            lambda p: p["demand_window"].update(compared_to="2026-06"),
            lambda p: self.skill(p)["windows"][-1].update(window="2026-Q4"),
            lambda p: self.skill(p)["forecast"].update(created="2026-Q2", resolves="2027-Q2"),
            lambda p: p["skill_map"]["top_level_adds"]["long_run_bar"].update(timing="leading"),
        ):
            payload = copy.deepcopy(self.payload)
            mutate(payload)
            self.reject(payload)
        fde = self.catalog.role("forward-deployed-engineer")
        fde["ladder"]["linked_roles"][0]["from_level"] = 5
        self.reject(fde, "unknown level")

    def test_monthly_record_keeps_four_quarter_forecasts(self) -> None:
        def monthly(value: object) -> object:
            if isinstance(value, dict):
                return {key: monthly(item) for key, item in value.items()}
            if isinstance(value, list):
                return [monthly(item) for item in value]
            return {"2026-Q2": "2026-06", "2026-Q3": "2026-07", "2027-Q3": "2027-07"}.get(value, value) if isinstance(value, str) else value

        payload = monthly(self.payload)
        payload["demand_window"]["window_length"] = "monthly"
        record = RoleRecord.from_dict(payload)
        self.assertEqual(record.demand_window.id, "2026-07")
        self.assertEqual(next(skill for skill in record.skill_map.skills if skill.id == "skill.aires").forecast.resolves, "2027-07")
        self.skill(payload)["forecast"]["resolves"] = "2027-06"
        self.reject(payload, "four quarters")

    def test_direct_model_validation_catches_mutations(self) -> None:
        record = RoleRecord.from_dict(self.payload)
        record.skill_map.skills[0].status = "fading"
        with self.assertRaises(RoleValidationError):
            validate_role(record)


class FixtureCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = json.loads((CONTRACT_ROOT / "prototype.json").read_text(encoding="utf-8"))
        cls.catalog = RoleCatalogStore.fixtures()

    def test_fixture_catalog_has_exact_prototype_roles_and_no_release_writes(self) -> None:
        records = self.catalog.list_roles()
        self.assertEqual({record["id"] for record in records}, {"role." + key for key in self.snapshot["roles"]})
        self.assertEqual(len(records), 3)
        self.assertEqual({record["data_kind"] for record in records}, {"fixture"})
        self.assertTrue(all("fixture" in record["release_id"] for record in records))
        self.assertFalse(FIXTURE_ROOT.resolve().is_relative_to((ROOT / "preview" / "release").resolve()))
        self.assertEqual(self.catalog.root, FIXTURE_ROOT)

    def test_fixture_source_dates_come_from_structured_prototype_years(self) -> None:
        for key, year in self.snapshot["onet_year"].items():
            record = self.catalog.role(key)
            sources = record["foundation"]["sources"] + [record["skill_map"]["top_level_adds"]["long_run_bar"]]
            sources += [source for skill in record["skill_map"]["skills"] for source in skill["evidence"]]
            for source in sources:
                self.assertEqual(source["as_of"], str(year) if source["source_kind"] == "onet_ratings" else None)

    def test_all_prototype_numbers_and_content_preserved(self) -> None:
        for key, raw in self.snapshot["roles"].items():
            with self.subTest(role=key):
                record = self.catalog.role(key)
                self.assertEqual(record["label"], raw["label"])
                self.assertEqual(record["anchors"], raw["anchors"])
                self.assertEqual(record["aliases"], raw["aliases"])
                self.assertEqual(record["status"], raw["status"])
                self.assertEqual(record["demand_window"]["postings"], raw["window"]["postings"])
                self.assertEqual(record["demand_window"]["employers"], raw["window"]["employers"])
                for field, source in (("median_pay", "wage"), ("employment", "employment"), ("outlook_10y", "outlook"), ("core_tasks", "tasks")):
                    self.assertEqual(record["foundation"][field], raw["foundation"][source])
                for level, original in zip(record["ladder"]["levels"], raw["levels"], strict=True):
                    self.assertEqual((level["level"], level["name"], level["evidence"]["postings"], level["evidence"]["employers"]),
                                     (original["L"], original["name"], original["n"], original["e"]))
                    self.assertEqual([item["label"] for item in level["expectations"]], [item[0] for item in original["exp"]])
                    for field, source in (("typical_titles", "titles"), ("scope", "scope"), ("owns", "owns"), ("step_up", "stepUp"), ("years_wording", "years")):
                        self.assertEqual(level[field], original[source])
                for skill, node in zip(record["skill_map"]["skills"], self.snapshot["graph"][key]["nodes"], strict=True):
                    self.assertEqual((skill["id"], skill["label"], skill["first_level"], skill["share_by_level"], skill["source_links"]),
                                     ("skill." + node[0], node[2], node[3], node[6], node[7]))
                    extra = self.snapshot["signals"][key].get(node[0], {})
                    self.assertEqual(skill["status"], extra.get("status", "core"))
                    if "call" in extra:
                        call = extra["call"]
                        self.assertEqual((skill["forecast"]["claim"], skill["forecast"]["probability"], skill["forecast"]["base_rate"], skill["forecast"]["resolves"]),
                                         (call[0], call[1] / 100, call[2] / 100, call[3]))
                expected_edges = [edge for edge in self.snapshot["graph"][key]["co"] if edge[2] >= 1.5 and edge[3] >= 25]
                self.assertEqual([[edge["a"].removeprefix("skill."), edge["b"].removeprefix("skill."), edge["lift"], edge["postings"]]
                                  for edge in record["skill_map"]["asked_together"]], expected_edges)
                for sector, original in zip(record["industries"], self.snapshot["industry"][key]["list"], strict=True):
                    self.assertEqual((sector["naics"], sector["evidence"]["postings"], sector["evidence"]["employers"]),
                                     (original["naics"], original["postings"], original["employers"]))
                    if not original.get("thin"):
                        profile = sector["profile"]
                        self.assertEqual((profile["median_pay"], profile["share_of_occupation_jobs"]), (original["pay"], original["share"]))
                        self.assertEqual([cert["posting_share"] for cert in profile["certifications"]], [cert[3] / 100 for cert in original["certs"]])
                for index, sector in enumerate(record["industries"]):
                    if sector["profile"] is not None:
                        self.assertEqual([share["share"] for share in sector["profile"]["skill_shares"]],
                                         [row[1][index] / 100 for row in self.snapshot["industry"][key]["heat"]])
                for observation, original in zip(record["observations"], raw["shifts"], strict=True):
                    self.assertEqual([[window["mentions"], window["postings"], window["employers"]] for window in observation["windows"]],
                                     [original["prev"], original["cur"]])

    def test_frozen_snapshot_matches_prototype_without_running_javascript(self) -> None:
        # Snapshot includes JS objects/arrays: normalize its very small JSON-like
        # literal grammar, without executing the prototype or using a browser.
        html = (ROOT / "design-concepts" / "role-atlas.html").read_text(encoding="utf-8")
        for variable, key in (("ROLES", "roles"), ("GRAPH", "graph"), ("INDUSTRY", "industry"), ("KINDS", "kinds"), ("ONET_YEAR", "onet_year"), ("SIGNALS", "signals")):
            with self.subTest(variable=variable):
                source = html.split("var " + variable + " = ", 1)[1].split(";\n", 1)[0]
                # Tokenize strings first so comments, URLs and key-like text in
                # prose cannot be altered by the bare-key/comment replacements.
                strings = []
                def save(match: re.Match) -> str:
                    strings.append(match[0])
                    return f"@@{len(strings) - 1}@@"
                source = re.sub(r'"(?:[^"\\]|\\.)*"', save, source)
                source = re.sub(r"//[^\n]*", "", source)
                source = re.sub(r"(?<=[{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", r'"\1":', source)
                source = re.sub(r"@@(\d+)@@", lambda match: strings[int(match[1])], source)
                self.assertEqual(json.loads(source), self.snapshot[key])

    def test_builder_is_deterministic_and_matches_checked_in_catalog(self) -> None:
        spec = importlib.util.spec_from_file_location("build_role_atlas_fixtures", ROOT / "scripts" / "build_role_atlas_fixtures.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        before = copy.deepcopy(self.snapshot)
        first = module.build_records(self.snapshot)
        second = module.build_records(self.snapshot)
        self.assertEqual([record.to_json() for record in first], [record.to_json() for record in second])
        self.assertEqual(self.snapshot, before)
        for record in first:
            self.assertEqual(record.to_json(), (FIXTURE_ROOT / f"{record.id.removeprefix('role.')}.json").read_text(encoding="utf-8"))

    def test_store_caches_validation_by_path_and_mtime_without_leaking_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payloads = [self.catalog.role(role) for role in ("account-executive", "hr-generalist")]
            for payload in payloads:
                (root / f"{payload['id']}.json").write_text(json.dumps(payload), encoding="utf-8")
            path = root / "role.account-executive.json"
            store = RoleCatalogStore(root)
            with patch.object(RoleRecord, "from_json", wraps=RoleRecord.from_json) as load:
                records = store.list_roles()
                self.assertEqual(load.call_count, 2)
                records[0]["skill_map"]["skills"][0]["label"] = "mutated"
                self.assertEqual(store.role("account-executive")["skill_map"]["skills"][0]["label"], payloads[0]["skill_map"]["skills"][0]["label"])
                self.assertEqual(load.call_count, 2)
                mtime = path.stat().st_mtime_ns
                payloads[0]["label"] = "Updated fixture label"
                path.write_text(json.dumps(payloads[0]), encoding="utf-8")
                os.utime(path, ns=(mtime + 1_000_000_000, mtime + 1_000_000_000))
                self.assertEqual(store.role("account-executive")["label"], "Updated fixture label")
                self.assertEqual(load.call_count, 3)
                mtime = path.stat().st_mtime_ns
                payloads[0]["publication"] = "experimental"
                path.write_text(json.dumps(payloads[0]), encoding="utf-8")
                os.utime(path, ns=(mtime + 1_000_000_000, mtime + 1_000_000_000))
                with self.assertRaises(RoleValidationError):
                    store.list_roles()
                self.assertEqual(load.call_count, 4)
                path.unlink()
                self.assertIsNone(store.role("account-executive"))
                self.assertEqual([record["id"] for record in store.list_roles()], ["role.hr-generalist"])
                self.assertEqual(load.call_count, 4)
                self.assertEqual(len(store._cache), 1)

    def test_store_pinning_unknowns_and_fresh_reads(self) -> None:
        release_id = self.catalog.list_roles()[0]["release_id"]
        first = self.catalog.role("account-executive", release_id)
        self.assertEqual(first, self.catalog.role("role.account-executive", release_id))
        first["label"] = "changed"
        self.assertEqual(self.catalog.role("account-executive")["label"], "Account Executive")
        self.assertIsNone(self.catalog.role("unknown"))
        self.assertIsNone(self.catalog.role("../../account-executive"))
        self.assertIsNone(self.catalog.role("account-executive", "unknown-release"))
        self.assertEqual(self.catalog.list_roles("unknown-release"), [])
        self.assertEqual(len(self.catalog.list_roles(release_id)), 3)
        with tempfile.TemporaryDirectory() as directory:
            store = RoleCatalogStore(Path(directory))
            self.assertEqual(store.list_roles(), [])
            self.assertIsNone(store.role("hr-generalist"))
            payload = self.catalog.role("hr-generalist")
            payload["data_kind"] = "research"
            path = Path(directory) / "hr.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(store.role("hr-generalist")["data_kind"], "research")
            duplicate = Path(directory) / "duplicate.json"
            duplicate.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(RoleValidationError, "duplicate role"):
                store.list_roles()
            duplicate.unlink()
            payload["publication"] = "bad-value"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(RoleValidationError):
                store.list_roles()


if __name__ == "__main__":
    unittest.main()
