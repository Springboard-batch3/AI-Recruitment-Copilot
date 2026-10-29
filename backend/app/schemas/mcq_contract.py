"""Pydantic schemas and contracts for Multiple Choice Questions (MCQ) in Phase 3F.2."""
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.phase3_enums import (
    AuthoringOrigin,
    QuestionDifficulty,
    QuestionFormat,
    ValidationState,
)


class StrictSchema(BaseModel):
    """Base schema rejecting extraneous fields."""
    model_config = ConfigDict(extra="forbid")


class FrozenSchema(BaseModel):
    """Immutable base schema rejecting extraneous fields."""
    model_config = ConfigDict(frozen=True, extra="forbid")


class McqOptionDto(StrictSchema):
    """A single option inside an MCQ."""
    option_id: str = Field(..., min_length=1, max_length=100)
    text: str = Field(..., min_length=1, max_length=500)


class McqItemDto(StrictSchema):
    """Recruiter-facing MCQ item with full answer key and explanation."""
    slot_id: UUID
    display_order: int
    question_format: QuestionFormat = QuestionFormat.MCQ_SINGLE_ANSWER
    question_text: str = Field(..., min_length=10, max_length=1200)
    options: List[McqOptionDto] = Field(..., min_length=2, max_length=5)
    correct_option_id: str = Field(..., min_length=1, max_length=100)
    answer_explanation: str = Field(..., min_length=1, max_length=1000)
    requirement_id: Optional[UUID] = None
    competency_name: str = Field(..., min_length=1, max_length=200)
    difficulty: QuestionDifficulty = QuestionDifficulty.FOUNDATIONAL
    authoring_origin: AuthoringOrigin = AuthoringOrigin.AI_GENERATED
    validation_status: ValidationState = ValidationState.PENDING
    safe_failure_code: Optional[str] = None
    updated_at: Optional[datetime] = None


class McqCreateRequest(StrictSchema):
    """Manual MCQ creation payload by recruiter."""
    question_text: str = Field(..., min_length=10, max_length=1200)
    options: List[McqOptionDto] = Field(..., min_length=2, max_length=5)
    correct_option_id: str = Field(..., min_length=1, max_length=100)
    answer_explanation: str = Field(..., min_length=1, max_length=1000)
    requirement_id: Optional[UUID] = None
    competency_name: Optional[str] = Field(default="General Competency", max_length=200)
    difficulty: QuestionDifficulty = QuestionDifficulty.FOUNDATIONAL


class McqUpdateRequest(StrictSchema):
    """Manual MCQ update payload by recruiter."""
    question_text: Optional[str] = Field(default=None, min_length=10, max_length=1200)
    options: Optional[List[McqOptionDto]] = Field(default=None, min_length=2, max_length=5)
    correct_option_id: Optional[str] = Field(default=None, min_length=1, max_length=100)
    answer_explanation: Optional[str] = Field(default=None, min_length=1, max_length=1000)
    requirement_id: Optional[UUID] = None
    competency_name: Optional[str] = Field(default=None, max_length=200)
    difficulty: Optional[QuestionDifficulty] = None


class McqGenerateRequest(StrictSchema):
    """Server-authoritative generation request for MCQs."""
    count: int = Field(default=5, ge=1, le=5)
    difficulty: QuestionDifficulty = QuestionDifficulty.FOUNDATIONAL
    requirement_ids: Optional[List[UUID]] = None


class McqReorderRequest(StrictSchema):
    """Reorder payload for MCQs in a question set."""
    slot_ids: List[UUID] = Field(..., min_length=1)


# Structured AI Provider Output Contract
class StructuredAiMcqOption(BaseModel):
    model_config = ConfigDict(extra="ignore")
    option_id: str
    text: str


class StructuredAiMcqQuestion(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question_text: str
    options: List[StructuredAiMcqOption]
    correct_option_id: str
    answer_explanation: str
    requirement_id: Optional[str] = None
    competency_id: Optional[str] = None
    difficulty: str = "FOUNDATIONAL"


class StructuredAiMcqBatchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    questions: List[StructuredAiMcqQuestion]


# Candidate-Safe Practice Discovery Contracts
class CandidateSafeMcqOption(FrozenSchema):
    """Safe option for candidates omitting correctness."""
    option_id: str
    text: str


class CandidateSafeMcqItem(FrozenSchema):
    """Safe MCQ item for candidates strictly omitting correct option and explanation."""
    slot_id: UUID
    display_order: int
    question_format: QuestionFormat = QuestionFormat.MCQ_SINGLE_ANSWER
    question_text: str
    options: List[CandidateSafeMcqOption]
    competency_name: str
    difficulty: QuestionDifficulty


class CandidatePracticeJobSummary(FrozenSchema):
    """Safe job discovery card for candidates."""
    release_id: UUID
    job_id: UUID
    job_version_id: Optional[UUID] = None
    job_title: str
    job_revision: Optional[int] = 1
    department: Optional[str] = None
    location: Optional[str] = None
    summary: Optional[str] = None
    conversation_question_count: int
    conversation_count: Optional[int] = None
    mcq_count: int
    total_question_count: Optional[int] = None
    released_at: datetime
    interview_mode: str = "TEXT"
    status_label: str = "Practice content ready"
    session_status: Optional[str] = None
    is_submitted: bool = False


class CandidatePracticeDiscoveryResponse(FrozenSchema):
    """List of active released practice jobs for authenticated candidate."""
    items: List[CandidatePracticeJobSummary]


# Phase 3F.3: Practice Diagnostic & Candidate Request Contracts

class PracticeEligibilityReasonCode(str):
    JOB_NOT_READY = "JOB_NOT_READY"
    NO_PRACTICE_SET = "NO_PRACTICE_SET"
    DRAFT_SET_EXISTS = "DRAFT_SET_EXISTS"
    APPROVED_SET_ON_OLDER_REVISION = "APPROVED_SET_ON_OLDER_REVISION"
    NO_VALID_QUESTIONS = "NO_VALID_QUESTIONS"
    ALREADY_RELEASED = "ALREADY_RELEASED"
    CANDIDATE_NOT_ELIGIBLE = "CANDIDATE_NOT_ELIGIBLE"
    ELIGIBLE = "ELIGIBLE"


class PracticeEligibleItem(StrictSchema):
    """Eligible practice set linked to exact locked job revision."""
    job_id: UUID
    job_revision_id: UUID
    job_title: str
    job_version: str  # e.g. "v3"
    question_set_id: UUID
    template_id: Optional[UUID] = None
    conversational_question_count: int
    mcq_question_count: int
    total_question_count: int
    status: str = "READY_TO_RELEASE"
    approved_at: Optional[datetime] = None
    has_candidate_request: bool = False
    candidate_request_id: Optional[UUID] = None


class PracticeIneligibleItem(StrictSchema):
    """Diagnostic detail for why a job revision cannot be released."""
    job_id: UUID
    job_revision_id: Optional[UUID] = None
    job_title: str
    job_version: str  # e.g. "v3"
    reason_code: str
    message: str
    question_set_id: Optional[UUID] = None
    template_id: Optional[UUID] = None
    older_revision: Optional[int] = None
    action: str  # e.g. "OPEN_QUESTION_SET", "PREPARE_PRACTICE_SET", "CREATE_PRACTICE_SET", "NONE"


class PracticeEligibleJob(StrictSchema):
    """Legacy backward-compatible adapter item for recruiter release modal."""
    job_id: UUID
    job_version_id: Optional[UUID] = None
    job_title: str
    job_revision: int
    ai_set_id: UUID
    set_version: int
    approved_at: datetime
    conversation_count: int
    mcq_count: int
    total_question_count: int
    already_released: bool
    has_candidate_request: bool = False
    candidate_request_id: Optional[UUID] = None


class PracticeEligibilityDiagnosticResponse(StrictSchema):
    """Structured diagnostic response containing both eligible and ineligible practice opportunities."""
    eligible: List[PracticeEligibleItem]
    ineligible: List[PracticeIneligibleItem]
    items: List[PracticeEligibleJob] = Field(default_factory=list)  # Backwards compatibility


class PracticeEligibleJobsResponse(StrictSchema):
    """Backward compatibility response model."""
    items: List[PracticeEligibleJob]


# Candidate Practice Catalog & Request Contracts

class CandidatePracticeCatalogItem(FrozenSchema):
    """Candidate-safe published catalog job revision item."""
    catalog_id: UUID
    job_id: UUID
    job_version_id: UUID
    safe_title: str
    safe_summary: Optional[str] = None
    skill_labels: List[str] = Field(default_factory=list)
    published_at: datetime
    request_status: str  # "NOT_REQUESTED", "REQUESTED", "RELEASED", "DECLINED"
    active_request_id: Optional[UUID] = None
    release_id: Optional[UUID] = None
    decline_reason: Optional[str] = None


class CandidatePracticeCatalogResponse(FrozenSchema):
    """List of candidate practice catalog entries."""
    items: List[CandidatePracticeCatalogItem]


class CandidatePracticeRequestCreatePayload(StrictSchema):
    """Candidate creates a practice request."""
    job_version_id: UUID


class CandidatePracticeRequestDeclinePayload(StrictSchema):
    """Recruiter declines a practice request."""
    decline_reason: Optional[str] = Field(default=None, max_length=500)


class CandidatePracticeRequestDto(StrictSchema):
    """Candidate practice request representation."""
    request_id: UUID
    organization_id: UUID
    candidate_id: UUID
    candidate_name: Optional[str] = None
    job_id: UUID
    job_version_id: UUID
    job_title: str
    job_revision: int
    status: str
    requested_at: datetime
    processed_at: Optional[datetime] = None
    resulting_release_id: Optional[UUID] = None
    decline_reason: Optional[str] = None


class PublishPracticeCatalogPayload(StrictSchema):
    """Recruiter publishes a job revision to the practice catalog."""
    job_version_id: UUID
    safe_title: Optional[str] = None
    safe_summary: Optional[str] = None
    skill_labels: Optional[List[str]] = None


