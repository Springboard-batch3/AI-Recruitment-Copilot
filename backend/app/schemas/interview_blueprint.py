from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.interview_snapshot import (
    DETERMINISTIC_BUILDER_VERSION,
    QUESTION_CATALOGUE_VERSION,
    RUBRIC_CATALOGUE_VERSION,
    SCORING_SEPARATION_POLICY,
    CandidatePlanInputSnapshot,
    CandidateRequirementResultSnapshotItem,
    JobBlueprintInputSnapshot,
    JobRequirementSnapshotItem,
    PolicyVersionReference,
    canonicalize_candidate_plan_snapshot,
    canonicalize_job_blueprint_snapshot,
    compute_candidate_plan_snapshot_hash,
    compute_job_blueprint_snapshot_hash,
)
from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    InterviewLayer,
    PlanLifecycle,
    QuestionType,
    ValidationState,
)
from app.schemas.safety_policy import (
    SAFETY_POLICY_HASH,
    SAFETY_POLICY_ID,
    SAFETY_POLICY_SCHEMA_VERSION,
    SAFETY_POLICY_VERSION,
    check_prohibited_topics,
)

BLUEPRINT_SCHEMA_VERSION: str = "1.0.0"


class BlueprintInputSnapshot(FrozenSchema):
    """Immutable input snapshot captured before building an interview blueprint."""
    organization_id: UUID
    job_id: UUID
    job_version_id: UUID
    revision: int = Field(ge=1)
    candidate_profile_id: Optional[UUID] = None
    candidate_profile_version: Optional[int] = Field(default=None, ge=1)
    match_result_id: Optional[UUID] = None
    match_result_version: Optional[int] = Field(default=None, ge=1)
    match_result_fingerprint: Optional[str] = Field(default=None, min_length=64, max_length=64)
    taxonomy_version: Optional[str] = None
    matcher_version: str = Field(min_length=1)
    allowlisted_requirement_ids: List[UUID] = Field(min_length=1)
    allowlisted_evidence_ids: List[str] = Field(default_factory=list)
    policy_reference: PolicyVersionReference = Field(default_factory=PolicyVersionReference)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_snapshot_invariants(self) -> "BlueprintInputSnapshot":
        # Check no prohibited keys in metadata
        verify_no_prohibited_keys(self.model_dump())
        # Ensure allowlisted requirement IDs are unique
        if len(self.allowlisted_requirement_ids) != len(set(self.allowlisted_requirement_ids)):
            raise ValueError("allowlisted_requirement_ids must not contain duplicates.")
        if len(self.allowlisted_evidence_ids) != len(set(self.allowlisted_evidence_ids)):
            raise ValueError("allowlisted_evidence_ids must not contain duplicates.")
        return self


class InterviewRubric(FrozenSchema):
    """Immutable scoring and evaluation rubric for an interview question."""
    rubric_id: UUID
    rubric_version: int = Field(default=1, ge=1)
    competency_definition: str = Field(min_length=5, max_length=1000)
    question_intent: str = Field(min_length=5, max_length=1000)
    observable_indicators: List[str] = Field(min_length=1)
    rating_anchors: Dict[int, str] = Field(
        description="Rating anchors for evaluation scale points (must include at least 1, 3, and 5)."
    )
    sufficient_evidence_guidance: str = Field(min_length=5, max_length=2000)
    insufficient_evidence_guidance: str = Field(min_length=5, max_length=2000)
    permitted_probes: List[str] = Field(default_factory=list)
    prohibited_inferences: List[str] = Field(default_factory=list)
    validation_state: ValidationState = ValidationState.PASSED
    policy_reference: PolicyVersionReference = Field(default_factory=PolicyVersionReference)
    schema_version: str = Field(default=BLUEPRINT_SCHEMA_VERSION)

    @model_validator(mode="after")
    def validate_rubric_invariants(self) -> "InterviewRubric":
        verify_no_prohibited_keys(self.model_dump())
        # Verify required scale points
        required_points = {1, 3, 5}
        if not required_points.issubset(set(self.rating_anchors.keys())):
            raise ValueError(f"rating_anchors must contain descriptions for at least scale points {required_points}.")
        for point, desc in self.rating_anchors.items():
            if not desc or len(desc.strip()) < 5:
                raise ValueError(f"Rating anchor for point {point} must be non-empty and at least 5 characters.")
        # Check text safety
        for text_val in [self.competency_definition, self.question_intent, self.sufficient_evidence_guidance, self.insufficient_evidence_guidance]:
            violation = check_prohibited_topics(text_val)
            if violation:
                raise ValueError(f"Prohibited topic '{violation[0]}' (matched '{violation[1]}') detected in rubric.")
        return self


class InterviewQuestion(FrozenSchema):
    """Immutable interview question contract."""
    question_id: UUID
    blueprint_id: UUID
    layer: InterviewLayer
    question_type: QuestionType
    competency_id: Optional[str] = Field(default=None, max_length=100)
    requirement_references: List[UUID] = Field(min_length=1)
    source_match_result_id: Optional[UUID] = None
    source_gap_report_id: Optional[UUID] = None
    deterministic_template_id: Optional[str] = None
    deterministic_template_version: Optional[str] = None
    question_text: str = Field(min_length=10, max_length=2000)
    question_intent: str = Field(min_length=5, max_length=1000)
    authoring_origin: AuthoringOrigin = AuthoringOrigin.DETERMINISTIC
    display_order: int = Field(ge=0)
    estimated_time_minutes: int = Field(ge=1, le=60)
    permitted_probes: List[str] = Field(default_factory=list)
    validation_state: ValidationState = ValidationState.PENDING
    included_in_common_score: bool = True
    rubric: Optional[InterviewRubric] = None
    policy_reference: PolicyVersionReference = Field(default_factory=PolicyVersionReference)
    schema_version: str = Field(default=BLUEPRINT_SCHEMA_VERSION)

    @model_validator(mode="after")
    def validate_question_invariants(self) -> "InterviewQuestion":
        verify_no_prohibited_keys(self.model_dump())
        # Invariant 1: Supplemental questions MUST be excluded from the common score
        if self.layer == InterviewLayer.SUPPLEMENTAL_EVIDENCE:
            if self.included_in_common_score:
                raise ValueError("Supplemental evidence questions MUST have included_in_common_score set to False.")
            if not self.source_match_result_id and not self.source_gap_report_id:
                raise ValueError("Supplemental evidence questions require a source_match_result_id or source_gap_report_id.")
        # Invariant 2: Common core questions MUST be included in the common score
        if self.layer == InterviewLayer.COMMON_CORE:
            if not self.included_in_common_score:
                raise ValueError("Common core questions MUST have included_in_common_score set to True.")
        # Invariant 3: Safety topic check
        for text_val in [self.question_text, self.question_intent] + self.permitted_probes:
            violation = check_prohibited_topics(text_val)
            if violation:
                raise ValueError(f"Prohibited topic '{violation[0]}' (matched '{violation[1]}') detected in question.")
        # Invariant 4: No duplicate requirement references
        if len(self.requirement_references) != len(set(self.requirement_references)):
            raise ValueError("requirement_references must not contain duplicate IDs.")
        return self


class InterviewBlueprint(FrozenSchema):
    """Immutable interview blueprint document container."""
    blueprint_id: UUID
    blueprint_version: int = Field(default=1, ge=1)
    input_snapshot: BlueprintInputSnapshot
    lifecycle_status: PlanLifecycle = PlanLifecycle.DRAFT
    common_core_questions: List[InterviewQuestion] = Field(min_length=1)
    supplemental_questions: List[InterviewQuestion] = Field(default_factory=list)
    total_estimated_minutes: int = Field(ge=1)
    authoring_origin: AuthoringOrigin = AuthoringOrigin.DETERMINISTIC
    approved_by_user_id: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    policy_reference: PolicyVersionReference = Field(default_factory=PolicyVersionReference)
    schema_version: str = Field(default=BLUEPRINT_SCHEMA_VERSION)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_blueprint_invariants(self) -> "InterviewBlueprint":
        verify_no_prohibited_keys(self.model_dump())
        # Verify common core layering
        for q in self.common_core_questions:
            if q.layer != InterviewLayer.COMMON_CORE:
                raise ValueError(f"Question '{q.question_id}' in common_core_questions must have layer=COMMON_CORE.")
        # Verify supplemental layering
        for q in self.supplemental_questions:
            if q.layer != InterviewLayer.SUPPLEMENTAL_EVIDENCE:
                raise ValueError(f"Question '{q.question_id}' in supplemental_questions must have layer=SUPPLEMENTAL_EVIDENCE.")
        # Verify approval invariants
        if self.lifecycle_status in {PlanLifecycle.APPROVED, PlanLifecycle.IN_USE}:
            if not self.approved_by_user_id or not self.approved_at:
                raise ValueError("Approved or In-Use blueprints require approved_by_user_id and approved_at.")
        return self
