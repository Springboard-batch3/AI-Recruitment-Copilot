"""Pydantic schemas for Phase 3D.5 Dedicated AI Interview Questions Workspace.

Zero-leakage guarantees:
- Never includes deterministic question wording or alternative text.
- Never includes reasoning_content, chain-of-thought, or raw prompts.
- Never includes provider secrets or raw provider JSON.
- Never exposes Phase 2 match scores or ranks.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


from app.schemas.phase3_enums import InterviewMode


class FrozenSchema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EligibleJobItemResponse(FrozenSchema):
    """Job selector option exposing minimum required fields only.
    
    Zero-leakage: does not expose full description or individual requirement text.
    """
    job_id: UUID
    job_title: str
    requirement_count: int
    current_ai_set_id: Optional[UUID] = None
    current_ai_set_status: Optional[str] = None
    applied_candidate_count: int = 0
    released_candidate_count: int = 0


class EligibleJobsResponse(FrozenSchema):
    """List of jobs eligible for AI question generation."""
    items: List[EligibleJobItemResponse]


class AiQuestionSetGenerateRequest(BaseModel):
    """Server-authoritative generation command payload."""
    model_config = ConfigDict(extra="forbid")
    job_id: UUID
    interview_mode: InterviewMode = InterviewMode.TEXT
    force_regenerate: bool = False


class SafeRubricResponse(FrozenSchema):
    """Safe rubric details without leaking sensitive internal calibration logic."""
    rubric_id: UUID
    rubric_version: int
    competency_definition: str
    question_intent: str
    observable_indicators: List[str] = Field(default_factory=list)
    rating_anchors: Dict[str, str] = Field(default_factory=dict)
    sufficient_evidence_guidance: Optional[str] = None
    insufficient_evidence_guidance: Optional[str] = None
    permitted_probes: List[str] = Field(default_factory=list)


class QuestionProvenanceDto(FrozenSchema):
    """Detailed provenance metadata for an AI practice question."""
    authoring_origin: str
    provider_class: Optional[str] = "LOCAL_OPENAI_COMPATIBLE"
    model_identifier: Optional[str] = None
    invocation_id: Optional[str] = None
    prompt_template_version: Optional[str] = "practice-question.v1"
    guardrail_policy_version: Optional[str] = "1.0.0"
    schema_version: Optional[str] = "practice-question.v1"
    generated_at: Optional[str] = None
    validation_status: Optional[str] = "PASSED"
    validation_result: Optional[str] = "PASSED"
    job_version_id: Optional[UUID] = None
    job_revision: Optional[int] = None
    source_requirement_ids: List[UUID] = Field(default_factory=list)
    set_version: Optional[int] = 1
    is_edited: bool = False
    reviewer_user_id: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None


class ReleaseEligibilitySummaryDto(FrozenSchema):
    """Release eligibility diagnosis and blocking items."""
    is_eligible: bool
    reason_code: Optional[str] = None
    safe_message: Optional[str] = None
    blocking_items: List[Dict[str, Any]] = Field(default_factory=list)


class AiQuestionSlotItemResponse(FrozenSchema):
    """Safe question slot inside an AI question set.
    
    Supports both CONVERSATION and MCQ_SINGLE_ANSWER question formats.
    Zero-leakage: deterministic_source_text and deterministic alternative texts
    are strictly omitted from this model.
    """
    slot_id: UUID
    display_order: int
    question_format: str = "CONVERSATION"  # "CONVERSATION" or "MCQ_SINGLE_ANSWER"
    competency_name: str
    question_type: str = "TECHNICAL"
    estimated_time_minutes: int = 5
    permitted_probes: List[str] = Field(default_factory=list)
    rubric: Optional[SafeRubricResponse] = None
    source_requirement_ids: List[UUID] = Field(default_factory=list)
    
    # AI question generation & review state
    ai_wording: Optional[str] = None
    question_text: Optional[str] = None  # Alias for ai_wording
    options: Optional[List[Dict[str, Any]]] = None
    correct_option_id: Optional[str] = None
    answer_explanation: Optional[str] = None
    difficulty: Optional[str] = None
    review_state: str = "UNREVIEWED"  # UNREVIEWED, KEPT, EDITED, REVISED, RECRUITER_AUTHORED
    authoring_origin: str = "LOCAL_LLM_GENERATED"
    validation_status: str = "PASSED"  # PASSED, PENDING, FAILED
    safe_failure_code: Optional[str] = None
    rationale_code: Optional[str] = None
    provenance: Optional[QuestionProvenanceDto] = None
    updated_at: Optional[datetime] = None


class AiQuestionSetDetailResponse(FrozenSchema):
    """Full detail view for the master-detail workspace /ai-interviews/:templateId or /ai-interviews/:aiSetId."""
    ai_set_id: UUID
    set_version: int
    interview_mode: InterviewMode = InterviewMode.TEXT
    template_id: Optional[UUID] = None
    source_template_version: Optional[int] = None
    source_mode: str = "BLUEPRINT_GROUNDED"  # BLUEPRINT_GROUNDED or REQUIREMENT_GROUNDED
    source_content_hash: str
    source_requirement_ids: List[UUID] = Field(default_factory=list)
    job_id: UUID
    job_title: str
    job_revision: int
    lifecycle_status: str  # DRAFT, STALE, VALIDATED, APPROVED
    is_immutable: bool
    content_hash: Optional[str] = None
    action: Optional[str] = None  # GENERATED or OPENED_EXISTING
    
    # Provenance metadata
    provider_class: Optional[str] = "LOCAL_OPENAI_COMPATIBLE"
    model_identifier: Optional[str] = None
    model_display_name: Optional[str] = None
    prompt_template_version: Optional[str] = "practice-question.v1"
    guardrail_policy_version: Optional[str] = "1.0.0"
    schema_version: Optional[str] = "practice-question.v1"
    predecessor_set_id: Optional[UUID] = None

    # Slots
    slots: List[AiQuestionSlotItemResponse] = Field(default_factory=list)
    total_slots_count: int
    generated_slots_count: int
    reviewed_slots_count: int
    conversation_count: int = 0
    mcq_count: int = 0
    
    # Staleness & Release Eligibility
    is_source_stale: bool = False
    stale_reason: Optional[str] = None
    release_eligibility: Optional[ReleaseEligibilitySummaryDto] = None
    is_legacy: bool = False
    
    created_at: datetime
    updated_at: datetime
    approved_at: Optional[datetime] = None


class AiQuestionSetSummaryResponse(FrozenSchema):
    """Card/row item for the /ai-interviews directory listing."""
    ai_set_id: UUID
    set_version: int
    interview_mode: InterviewMode = InterviewMode.TEXT
    template_id: Optional[UUID] = None
    source_mode: str = "BLUEPRINT_GROUNDED"
    job_id: UUID
    job_title: str
    lifecycle_status: str
    is_immutable: bool
    is_source_stale: bool = False
    is_legacy: bool = False
    model_display_name: Optional[str] = None
    generated_question_count: int
    total_blueprint_slots: int
    conversation_count: int = 0
    mcq_count: int = 0
    applied_candidate_count: int = 0
    released_candidate_count: int = 0
    created_at: datetime
    updated_at: datetime


class AiQuestionSetDirectoryResponse(FrozenSchema):
    """Directory listing response with status counts."""
    sets: List[AiQuestionSetSummaryResponse]
    total_count: int
    awaiting_review_count: int
    ready_for_approval_count: int
    approved_count: int


class AiQuestionSlotUpdateRequest(BaseModel):
    """Request to update/review a single question slot."""
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(..., description="KEPT, EDITED, REVISED, MANUAL_AUTHOR")
    wording: Optional[str] = Field(default=None, max_length=1500)
    reason: Optional[str] = Field(default=None, max_length=500)


class AiQuestionSetValidateResponse(FrozenSchema):
    """Result of validating an AI question set."""
    is_valid: bool
    lifecycle_status: str
    blocking_errors: List[str] = Field(default_factory=list)
    safe_message: str
    release_eligibility: Optional[ReleaseEligibilitySummaryDto] = None


class AiQuestionSetApproveResponse(FrozenSchema):
    """Result of approving and sealing an AI question set."""
    ai_set_id: UUID
    set_version: int
    lifecycle_status: str = "APPROVED"
    is_immutable: bool = True
    content_hash: str
    approved_at: datetime
    safe_message: str

