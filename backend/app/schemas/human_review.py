from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys


# =========================================================================
# Phase 3D.3 Request Contracts
# =========================================================================

class AcceptReviewRequest(FrozenSchema):
    """Command to accept provisional assessment without alteration."""
    expected_review_version: Optional[int] = Field(default=None, ge=1)


class EditReviewRequest(FrozenSchema):
    """Command to edit observation while maintaining valid evidence citations."""
    expected_review_version: Optional[int] = Field(default=None, ge=1)
    reviewed_observation: str = Field(min_length=5, max_length=3000)
    cited_segment_ids: List[str] = Field(min_length=1)
    reason: Optional[str] = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_content(self) -> "EditReviewRequest":
        verify_no_prohibited_keys(self.model_dump())
        obs = self.reviewed_observation.strip()
        if not obs:
            raise ValueError("Reviewed observation must not be blank.")
        # Reject raw HTML or script tags
        if "<script" in obs.lower() or "javascript:" in obs.lower() or "<html" in obs.lower():
            raise ValueError("Raw script or HTML content is strictly prohibited in reviewed observations.")
        return self


class OverrideReviewRequest(FrozenSchema):
    """Command to override the rubric level, requiring an explicit reason."""
    expected_review_version: Optional[int] = Field(default=None, ge=1)
    reviewed_rubric_level: int = Field(ge=1, le=5)
    reason: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def validate_content(self) -> "OverrideReviewRequest":
        verify_no_prohibited_keys(self.model_dump())
        if not self.reason.strip():
            raise ValueError("Override requires a non-empty rationale reason.")
        return self


class RejectReviewRequest(FrozenSchema):
    """Command to reject the provisional observation with mandatory justification."""
    expected_review_version: Optional[int] = Field(default=None, ge=1)
    reason: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def validate_content(self) -> "RejectReviewRequest":
        verify_no_prohibited_keys(self.model_dump())
        if not self.reason.strip():
            raise ValueError("Rejection requires a non-empty justification reason.")
        return self


class MarkInsufficientRequest(FrozenSchema):
    """Command to record insufficient evidence as a neutral outcome (not scored as zero)."""
    expected_review_version: Optional[int] = Field(default=None, ge=1)
    reason: Optional[str] = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_content(self) -> "MarkInsufficientRequest":
        verify_no_prohibited_keys(self.model_dump())
        return self


class FinalizeAssessmentRequest(FrozenSchema):
    """Command to finalize and lock the interview assessment immutably."""
    expected_attempt_version: Optional[int] = Field(default=None, ge=1)
    reason: Optional[str] = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_content(self) -> "FinalizeAssessmentRequest":
        verify_no_prohibited_keys(self.model_dump())
        return self


# =========================================================================
# Phase 3D.3 Response Contracts
# =========================================================================

class AssessmentReviewItemResponse(FrozenSchema):
    """Authoritative API DTO for a recorded human review."""
    review_id: UUID
    assessment_id: UUID
    attempt_id: UUID
    question_id: UUID
    reviewer_user_id: UUID
    review_version: int
    decision: str  # 'ACCEPTED', 'EDITED', 'OVERRIDDEN', 'REJECTED', 'INSUFFICIENT'
    reviewed_observation: Optional[str] = None
    reviewed_rubric_level: Optional[int] = None
    retained_citations: List[str] = Field(default_factory=list)
    reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class DeterministicAggregateResponse(FrozenSchema):
    """Deterministic core interview aggregate summary."""
    policy_version: str = "1.0.0"
    policy_hash: str = ""
    aggregate_score: Optional[float] = None
    numerator: float = 0.0
    denominator: float = 0.0
    total_core_questions: int = 0
    scored_core_questions: int = 0
    included_items: List[Dict[str, Any]] = Field(default_factory=list)
    excluded_items: List[Dict[str, Any]] = Field(default_factory=list)


class FinalizationEligibilityResponse(FrozenSchema):
    """Status indicating whether an attempt can be finalized."""
    is_eligible: bool
    blocking_reasons: List[str] = Field(default_factory=list)
    total_core_questions: int = 0
    reviewed_core_questions: int = 0
    unreviewed_question_ids: List[str] = Field(default_factory=list)
    aggregate_preview: Optional[DeterministicAggregateResponse] = None


class InterviewFinalizeResponse(FrozenSchema):
    """Immutable result of finalization."""
    interview_assessment_id: UUID
    attempt_id: UUID
    lifecycle_status: str = "FINALIZED"
    is_finalized: bool = True
    finalized_at: datetime
    finalized_by_user_id: UUID
    aggregate: DeterministicAggregateResponse
    safe_message: str


class ReviewWorkspaceQuestionItem(FrozenSchema):
    """Full question details for the evidence-first review workspace."""
    question_id: UUID
    question_version: int
    display_order: int
    layer: str
    question_type: str
    competency_name: str
    question_text: str
    submitted_answer: Optional[str] = None
    answer_segments: List[Dict[str, Any]] = Field(default_factory=list)
    rubric: Optional[Dict[str, Any]] = None
    provisional: Optional[Dict[str, Any]] = None
    human_review: Optional[AssessmentReviewItemResponse] = None


class ReviewWorkspaceResponse(FrozenSchema):
    """Aggregated workspace data for recruiter evidence review."""
    attempt_id: UUID
    lifecycle_status: str
    candidate_id: UUID
    candidate_name: Optional[str] = None
    job_id: UUID
    job_title: Optional[str] = None
    questions: List[ReviewWorkspaceQuestionItem] = Field(default_factory=list)
    eligibility: FinalizationEligibilityResponse
    finalized_assessment: Optional[InterviewFinalizeResponse] = None
