"""Extractor registry invariants.

Every extractor must be self-describing. These are the fields a result needs in order to
be reproducible and auditable later, so a missing one is a defect, not a style issue.
"""

from __future__ import annotations

import pytest

from mp2_extractors import ALL_EXTRACTORS, content_hash
from mp2_extractors.registry import ExtractorSpec


@pytest.mark.parametrize("spec", ALL_EXTRACTORS, ids=lambda s: s.extractor_id)
def test_every_extractor_is_fully_described(spec: ExtractorSpec) -> None:
    assert spec.extractor_id
    assert spec.version
    assert spec.repeatability_class in {"D0", "D1", "D2"}
    assert spec.input_schema_version
    assert spec.output_schema_version
    assert spec.hardware_class
    assert spec.license_record != "unrecorded", (
        f"{spec.extractor_id} must carry a license record before it can be used"
    )


def test_extractor_identities_are_unique() -> None:
    identities = [spec.identity for spec in ALL_EXTRACTORS]
    assert len(identities) == len(set(identities))


def test_content_hash_is_order_independent_and_stable() -> None:
    a = {"beta": [1, 2, 3], "alpha": {"x": 1.5, "y": None}}
    b = {"alpha": {"y": None, "x": 1.5}, "beta": [1, 2, 3]}
    assert content_hash(a) == content_hash(b)
    assert content_hash(a) != content_hash({**a, "beta": [3, 2, 1]})
    # 64 hex characters, matching the ModelGatewayResponse content_hash contract.
    assert len(content_hash(a)) == 64


def test_with_parameters_does_not_mutate_the_registry() -> None:
    spec = ALL_EXTRACTORS[0]
    original = dict(spec.parameters)
    derived = spec.with_parameters(threshold=99)
    assert derived.parameters["threshold"] == 99
    assert spec.parameters == original
    assert derived.extractor_id == spec.extractor_id
