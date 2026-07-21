from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from pawbench_agentscope._atomic_io import read_text_no_follow
from pawbench_agentscope.features import FEATURE_IDS, TAXONOMY_VERSION


DOMAIN_PROFILE_SCHEMA_VERSION = "harness-core-domain-profiles/v1"
DEFAULT_DOMAIN_PROFILE_PATH = Path(__file__).with_name("domain_profiles.json")
DOMAIN_CODES = ("UA", "WS", "MA")
MAX_DOMAIN_PROFILE_BYTES = 1024 * 1024


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


class PriorityFeatureGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    feature_ids: list[str] = Field(min_length=1)


class DomainProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    display_name: str = Field(min_length=1)
    scene: str = Field(min_length=1)
    family_prefix: str = Field(pattern=r"^[a-z][a-z0-9]*$")
    known_v2_prefixes: list[str] = Field(min_length=1)
    priority_feature_groups: list[PriorityFeatureGroup] = Field(min_length=1)
    evidence_checks: list[str] = Field(min_length=1)

    @property
    def priority_feature_ids(self) -> tuple[str, ...]:
        return tuple(
            feature_id
            for group in self.priority_feature_groups
            for feature_id in group.feature_ids
        )


class DomainProfileCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    taxonomy_version: str
    prioritization_only: bool
    profiles: list[DomainProfile]

    def by_code(self) -> dict[str, DomainProfile]:
        return {profile.code: profile for profile in self.profiles}


def _validate_catalog(catalog: DomainProfileCatalog) -> None:
    if catalog.schema_version != DOMAIN_PROFILE_SCHEMA_VERSION:
        raise ValueError(
            f"domain profile schema must be {DOMAIN_PROFILE_SCHEMA_VERSION}; "
            f"got {catalog.schema_version}"
        )
    if catalog.taxonomy_version != TAXONOMY_VERSION:
        raise ValueError(
            f"domain profiles require taxonomy {TAXONOMY_VERSION}; "
            f"got {catalog.taxonomy_version}"
        )
    if catalog.prioritization_only is not True:
        raise ValueError("domain profiles must remain prioritization-only")

    codes = tuple(profile.code for profile in catalog.profiles)
    if codes != DOMAIN_CODES:
        raise ValueError(f"domain profile order must be {DOMAIN_CODES}; got {codes}")

    known_features = set(FEATURE_IDS)
    seen_prefixes: set[str] = set()
    for profile in catalog.profiles:
        if profile.family_prefix != profile.code.lower():
            raise ValueError(
                f"{profile.code}.family_prefix must be {profile.code.lower()!r}"
            )
        feature_ids = profile.priority_feature_ids
        unknown = sorted(set(feature_ids) - known_features)
        if unknown:
            raise ValueError(f"{profile.code} references unknown Features: {unknown}")
        if len(feature_ids) != len(set(feature_ids)):
            raise ValueError(f"{profile.code} repeats a priority Feature")

        for prefix in profile.known_v2_prefixes:
            if prefix != prefix.lower() or not prefix.startswith(f"{profile.family_prefix}-"):
                raise ValueError(f"invalid {profile.code} V2 prefix: {prefix!r}")
            if prefix in seen_prefixes:
                raise ValueError(f"duplicate V2 prefix: {prefix!r}")
            seen_prefixes.add(prefix)

    if len(seen_prefixes) != 8:
        raise ValueError(f"expected the 8 observed V2 prefixes; got {len(seen_prefixes)}")


def load_domain_profiles(path: str | Path | None = None) -> DomainProfileCatalog:
    source = Path(path) if path is not None else DEFAULT_DOMAIN_PROFILE_PATH
    try:
        value = json.loads(
            read_text_no_follow(source, max_bytes=MAX_DOMAIN_PROFILE_BYTES),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"cannot load domain profile catalog {source}: {exc}") from exc
    catalog = DomainProfileCatalog.model_validate(value)
    _validate_catalog(catalog)
    return catalog


def profile_for_task(
    task_id: str,
    catalog: DomainProfileCatalog | None = None,
) -> DomainProfile:
    normalized = task_id.strip().lower()
    if not normalized:
        raise ValueError("task_id must be non-empty")
    profiles = catalog or load_domain_profiles()
    for profile in profiles.profiles:
        if normalized.startswith(f"{profile.family_prefix}-"):
            return profile
    raise ValueError(f"task_id has no UA/WS/MA family prefix: {task_id!r}")


__all__ = [
    "DEFAULT_DOMAIN_PROFILE_PATH",
    "DOMAIN_CODES",
    "DOMAIN_PROFILE_SCHEMA_VERSION",
    "DomainProfile",
    "DomainProfileCatalog",
    "PriorityFeatureGroup",
    "load_domain_profiles",
    "profile_for_task",
]
