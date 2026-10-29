import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field, model_validator

from app.models.job import (
    RequirementCategory,
    RequirementNecessity,
    RequirementPriority,
)
from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys
from app.schemas.phase3_enums import InterviewLayer, PlanLifecycle
from app.schemas.requirement_gap import RequirementGapState
from app.schemas.requirement_match import MatchResultState
from app.schemas.safety_policy import (
    SAFETY_POLICY_HASH,
    SAFETY_POLICY_ID,
    SAFETY_POLICY_SCHEMA_VERSION,
    SAFETY_POLICY_VERSION,
)

DETERMINISTIC_BUILDER_VERSION: str = "1.0.0"
QUESTION_CATALOGUE_VERSION: str = "1.0.0"
RUBRIC_CATALOGUE_VERSION: str = "1.0.0"
SCORING_SEPARATION_POLICY: str = "STRICT_COMMON_CORE_SEPARATION"


class PolicyVersionReference(FrozenSchema):
    """Immutable reference to an authoritative versioned safety policy."""

    policy_id: str = Field(default=SAFETY_POLICY_ID, min_length=1)
    policy_version: str = Field(default=SAFETY_POLICY_VERSION, min_length=1)
    policy_hash: str = Field(default=SAFETY_POLICY_HASH, min_length=64, max_length=64)
    schema_version: str = Field(default=SAFETY_POLICY_SCHEMA_VERSION, min_length=1)


class JobRequirementSnapshotItem(FrozenSchema):
    """Immutable normalized requirement item inside an authoritative job blueprint snapshot."""

    requirement_id: UUID
    display_order: int = Field(ge=0)
    category: RequirementCategory
    label: str = Field(min_length=1, max_length=255)
    normalized_label: str = Field(min_length=1, max_length=255)
    necessity: RequirementNecessity
    priority: RequirementPriority
    weight: Optional[float] = Field(default=None, ge=0.0, le=10.0)
    must_have_rationale: Optional[str] = Field(default=None, max_length=2000)
    min_experience_months: Optional[int] = Field(default=None, ge=1)
    recency_years: Optional[int] = Field(default=None, ge=1, le=50)
    accepted_evidence_types: List[str] = Field(default_factory=list)
    accepted_equivalents: List[str] = Field(default_factory=list)
    taxonomy_skill_concept_id: Optional[UUID] = None
    taxonomy_canonical_label: Optional[str] = None
    taxonomy_external_id: Optional[str] = None


class JobBlueprintInputSnapshot(FrozenSchema):
    """Authoritative server-loaded immutable input snapshot for job interview blueprint generation."""

    organization_id: UUID
    job_id: UUID
    job_version_id: UUID
    revision: int = Field(ge=1)
    job_readiness_state: str = Field(min_length=1)
    approved_requirements: List[JobRequirementSnapshotItem] = Field(min_length=1)
    occupation_concept_id: Optional[UUID] = None
    occupation_title: Optional[str] = None
    occupation_soc_code: Optional[str] = None
    policy_reference: PolicyVersionReference = Field(default_factory=PolicyVersionReference)
    deterministic_builder_version: str = Field(default=DETERMINISTIC_BUILDER_VERSION)
    question_catalogue_version: str = Field(default=QUESTION_CATALOGUE_VERSION)
    rubric_catalogue_version: str = Field(default=RUBRIC_CATALOGUE_VERSION)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_job_snapshot_invariants(self) -> "JobBlueprintInputSnapshot":
        verify_no_prohibited_keys(self.model_dump())
        req_ids = [r.requirement_id for r in self.approved_requirements]
        if len(req_ids) != len(set(req_ids)):
            raise ValueError("approved_requirements must contain unique requirement IDs.")
        return self


class CandidateRequirementResultSnapshotItem(FrozenSchema):
    """Immutable requirement-level match result and gap state inside a candidate plan snapshot."""

    requirement_id: UUID
    match_state: MatchResultState
    match_value: float = Field(ge=0.0, le=1.0)
    gap_state: Optional[RequirementGapState] = None
    shortfall_reason_code: Optional[str] = None
    allowlisted_evidence_ids: List[str] = Field(default_factory=list)
    is_conflict: bool = False
    is_unverified: bool = False


class CandidatePlanInputSnapshot(FrozenSchema):
    """Authoritative server-loaded immutable input snapshot for candidate-tailored interview plan generation."""

    job_snapshot: JobBlueprintInputSnapshot
    candidate_id: UUID
    profile_id: UUID
    profile_version: int = Field(ge=1)
    match_run_id: UUID
    match_result_id: UUID
    match_result_fingerprint: str = Field(min_length=64, max_length=64)
    match_score: float = Field(ge=0.0, le=100.0)
    confidence_tier: str = Field(min_length=1)
    requirement_results: List[CandidateRequirementResultSnapshotItem] = Field(min_length=1)
    allowlisted_requirement_ids: List[UUID] = Field(min_length=1)
    allowlisted_evidence_ids: List[str] = Field(default_factory=list)
    scoring_separation_policy: str = Field(default=SCORING_SEPARATION_POLICY)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_candidate_snapshot_invariants(self) -> "CandidatePlanInputSnapshot":
        verify_no_prohibited_keys(self.model_dump())
        if len(self.allowlisted_requirement_ids) != len(set(self.allowlisted_requirement_ids)):
            raise ValueError("allowlisted_requirement_ids must not contain duplicate IDs.")
        if len(self.allowlisted_evidence_ids) != len(set(self.allowlisted_evidence_ids)):
            raise ValueError("allowlisted_evidence_ids must not contain duplicate evidence IDs.")
        return self


# Canonicalization and Hashing Utilities

def _normalize_item_for_hashing(obj: Any) -> Any:
    """Recursively converts structures into sorted, deterministic JSON-serializable primitives."""
    if isinstance(obj, dict):
        return {k: _normalize_item_for_hashing(v) for k, v in sorted(obj.items()) if k != "captured_at"}
    if isinstance(obj, (list, tuple, set)):
        return [_normalize_item_for_hashing(v) for v in obj]
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, (float, Decimal)):
        return round(float(obj), 6)
    if hasattr(obj, "value"):
        return obj.value
    if hasattr(obj, "model_dump"):
        return _normalize_item_for_hashing(obj.model_dump(mode="json"))
    return obj


def canonicalize_job_blueprint_snapshot(snapshot: JobBlueprintInputSnapshot) -> Dict[str, Any]:
    """Produces a deterministic, canonical dictionary representation of a JobBlueprintInputSnapshot."""
    reqs_sorted = sorted(
        snapshot.approved_requirements,
        key=lambda r: (r.display_order, str(r.requirement_id)),
    )
    reqs_normalized = []
    for r in reqs_sorted:
        reqs_normalized.append({
            "accepted_equivalents": sorted(r.accepted_equivalents),
            "accepted_evidence_types": sorted(r.accepted_evidence_types),
            "category": r.category.value if hasattr(r.category, "value") else str(r.category),
            "display_order": r.display_order,
            "label": r.label.strip(),
            "min_experience_months": r.min_experience_months,
            "must_have_rationale": r.must_have_rationale.strip() if r.must_have_rationale else None,
            "necessity": r.necessity.value if hasattr(r.necessity, "value") else str(r.necessity),
            "normalized_label": r.normalized_label.strip(),
            "priority": r.priority.value if hasattr(r.priority, "value") else str(r.priority),
            "recency_years": r.recency_years,
            "requirement_id": str(r.requirement_id),
            "taxonomy_canonical_label": r.taxonomy_canonical_label,
            "taxonomy_external_id": r.taxonomy_external_id,
            "taxonomy_skill_concept_id": str(r.taxonomy_skill_concept_id) if r.taxonomy_skill_concept_id else None,
            "weight": round(float(r.weight), 6) if r.weight is not None else None,
        })

    return {
        "approved_requirements": reqs_normalized,
        "deterministic_builder_version": snapshot.deterministic_builder_version,
        "job_id": str(snapshot.job_id),
        "job_readiness_state": snapshot.job_readiness_state,
        "job_version_id": str(snapshot.job_version_id),
        "occupation_concept_id": str(snapshot.occupation_concept_id) if snapshot.occupation_concept_id else None,
        "occupation_soc_code": snapshot.occupation_soc_code,
        "occupation_title": snapshot.occupation_title,
        "organization_id": str(snapshot.organization_id),
        "policy_reference": {
            "policy_hash": snapshot.policy_reference.policy_hash,
            "policy_id": snapshot.policy_reference.policy_id,
            "policy_version": snapshot.policy_reference.policy_version,
            "schema_version": snapshot.policy_reference.schema_version,
        },
        "question_catalogue_version": snapshot.question_catalogue_version,
        "revision": snapshot.revision,
        "rubric_catalogue_version": snapshot.rubric_catalogue_version,
    }


def compute_job_blueprint_snapshot_hash(snapshot: JobBlueprintInputSnapshot) -> str:
    """Computes deterministic SHA-256 hash of a JobBlueprintInputSnapshot."""
    canonical_dict = canonicalize_job_blueprint_snapshot(snapshot)
    serialized = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().lower()


def canonicalize_candidate_plan_snapshot(snapshot: CandidatePlanInputSnapshot) -> Dict[str, Any]:
    """Produces a deterministic, canonical dictionary representation of a CandidatePlanInputSnapshot."""
    canonical_job = canonicalize_job_blueprint_snapshot(snapshot.job_snapshot)
    req_results_sorted = sorted(
        snapshot.requirement_results,
        key=lambda rr: str(rr.requirement_id),
    )
    req_results_normalized = []
    for rr in req_results_sorted:
        req_results_normalized.append({
            "allowlisted_evidence_ids": sorted(rr.allowlisted_evidence_ids),
            "gap_state": rr.gap_state.value if hasattr(rr.gap_state, "value") and rr.gap_state else (str(rr.gap_state) if rr.gap_state else None),
            "is_conflict": rr.is_conflict,
            "is_unverified": rr.is_unverified,
            "match_state": rr.match_state.value if hasattr(rr.match_state, "value") else str(rr.match_state),
            "match_value": round(float(rr.match_value), 6),
            "requirement_id": str(rr.requirement_id),
            "shortfall_reason_code": rr.shortfall_reason_code,
        })

    return {
        "allowlisted_evidence_ids": sorted(snapshot.allowlisted_evidence_ids),
        "allowlisted_requirement_ids": sorted([str(rid) for rid in snapshot.allowlisted_requirement_ids]),
        "candidate_id": str(snapshot.candidate_id),
        "confidence_tier": snapshot.confidence_tier,
        "job_snapshot": canonical_job,
        "match_result_fingerprint": snapshot.match_result_fingerprint,
        "match_result_id": str(snapshot.match_result_id),
        "match_run_id": str(snapshot.match_run_id),
        "match_score": round(float(snapshot.match_score), 6),
        "profile_id": str(snapshot.profile_id),
        "profile_version": snapshot.profile_version,
        "requirement_results": req_results_normalized,
        "scoring_separation_policy": snapshot.scoring_separation_policy,
    }


def compute_candidate_plan_snapshot_hash(snapshot: CandidatePlanInputSnapshot) -> str:
    """Computes deterministic SHA-256 hash of a CandidatePlanInputSnapshot."""
    canonical_dict = canonicalize_candidate_plan_snapshot(snapshot)
    serialized = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().lower()
