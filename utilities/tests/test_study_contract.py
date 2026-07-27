"""Coverage for the dependency-light materialized-study contract."""

from __future__ import annotations

import copy
import re

import pytest

from models.study import (
    STUDY_SCHEMA_NAME,
    STUDY_SCHEMA_VERSION,
    StudyValidationError,
    catalog_item_from_study,
    validate_catalog,
    validate_study_record,
)


def _variation(variation_id: str, *, scan: dict | None = None) -> dict:
    return {
        "id": variation_id,
        "name": f"Variation {variation_id}",
        "thesis": "A concrete, preserved research thesis.",
        "methodology": {
            "summary": "A fixed historical test.",
            "population": "Eligible instruments with complete observations.",
            "inclusionCriteria": ["Meets the frozen inclusion rule."],
            "exclusionCriteria": ["Missing observations are excluded."],
            "features": ["Relative performance."],
            "endpoint": "Mean forward excess return.",
            "controls": ["Frozen benchmark comparison."],
            "period": "2020-01-01 through 2021-01-01.",
            "inference": "Pre-specified interval estimate.",
            "limitations": ["Historical evidence is not a forecast."],
        },
        "outcome": {
            "verdict": "FAILED",
            "evidenceLevel": "CONFIRMATORY",
            "summary": "The primary endpoint did not establish the thesis.",
            "whatWorked": ["The fixed pipeline completed."],
            "whatDidNotWork": ["The interval included zero."],
            "nextSteps": ["Keep the result visible without claiming an edge."],
            "moreData": {"assessment": False, "rationale": "The endpoint was already spent."},
        },
        "stats": [{
            "id": "mean-forward-excess-return",
            "label": "Mean forward excess return",
            "value": -0.002,
            "format": "PERCENT",
            "precision": 2,
            "scope": "Primary endpoint",
            "confidenceInterval": {"level": 0.95, "low": -0.01, "high": 0.004},
            "interpretation": "The interval includes zero.",
            "priority": "PRIMARY",
        }],
        "scan": scan,
        "provenance": {
            "specificationPath": "studies/example/definition.json",
            "artifactPath": "data/example/run/summary.json",
            "runId": "example-run",
            "sourceCommit": None,
            "generatedAt": "2026-07-26T00:00:00Z",
            "dataCutoff": "2021-01-01",
            "verificationState": "VERIFIED",
        },
        "caveats": ["No operational result changes the historical outcome."],
    }


@pytest.fixture
def valid_study() -> dict:
    return {
        "schemaName": STUDY_SCHEMA_NAME,
        "schemaVersion": STUDY_SCHEMA_VERSION,
        "id": "example-study",
        "name": "Example Study",
        "summary": "A complete materialized study fixture.",
        "updatedAt": "2026-07-26T00:00:00Z",
        "defaultVariationId": "base",
        "variations": [
            _variation("base", scan={
                "executionSupported": True,
                "scanType": "example-current-scan",
                "resultSchema": "example-candidates-v1",
                "eligibilityExplanation": "The scan applies the studied selection rules.",
                "warning": "Candidates do not change the failed historical verdict.",
                "latestSnapshot": None,
            }),
            _variation("exploratory"),
        ],
        "futurePublisherField": {"isIgnoredByV1Reader": True},
    }


def test_complete_two_variation_fixture_validates_and_projects_catalog(valid_study):
    validate_study_record(valid_study)
    catalog_item = catalog_item_from_study(valid_study)
    assert catalog_item == {
        "id": "example-study",
        "name": "Example Study",
        "summary": "A complete materialized study fixture.",
        "defaultVariationId": "base",
        "variationCount": 2,
        "verdict": "FAILED",
        "evidenceLevel": "CONFIRMATORY",
        "scanAvailable": True,
        "updatedAt": "2026-07-26T00:00:00Z",
    }
    validate_catalog({
        "schemaName": STUDY_SCHEMA_NAME,
        "schemaVersion": STUDY_SCHEMA_VERSION,
        "studies": [catalog_item],
        "futureCatalogField": "ignored",
    })


@pytest.mark.parametrize(("mutate", "path"), [
    (lambda study: study.pop("summary"), "$.summary"),
    (lambda study: study.__setitem__("defaultVariationId", "missing"), "$.defaultVariationId"),
    (lambda study: study["variations"][1].__setitem__("id", "base"), "$.variations[1].id"),
    (lambda study: study["variations"][0]["outcome"].__setitem__("verdict", "WON"),
     "$.variations[0].outcome.verdict"),
    (lambda study: study["variations"][0]["stats"][0].__setitem__("precision", -1),
     "$.variations[0].stats[0].precision"),
    (lambda study: study["variations"][0]["scan"].__setitem__("scanType", "Not a type"),
     "$.variations[0].scan.scanType"),
    (lambda study: study["variations"][0]["provenance"].__setitem__("generatedAt", "yesterday"),
     "$.variations[0].provenance.generatedAt"),
])
def test_invalid_study_states_fail_with_actionable_paths(valid_study, mutate, path):
    invalid = copy.deepcopy(valid_study)
    mutate(invalid)
    with pytest.raises(StudyValidationError, match=re.escape(path)):
        validate_study_record(invalid)


def test_catalog_rejects_invalid_evidence_label():
    with pytest.raises(StudyValidationError, match=r"\$\.studies\[0\]\.evidenceLevel"):
        validate_catalog({
            "schemaName": STUDY_SCHEMA_NAME,
            "schemaVersion": STUDY_SCHEMA_VERSION,
            "studies": [{
                "id": "example-study", "name": "Example", "summary": "Summary",
                "defaultVariationId": "base", "variationCount": 1, "verdict": "FAILED",
                "evidenceLevel": "INVALID", "scanAvailable": False,
                "updatedAt": "2026-07-26T00:00:00Z",
            }],
        })
