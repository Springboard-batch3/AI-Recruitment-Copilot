"""Authoritative Pydantic Schemas for Phase 4C.4: Recruiter Evaluation Workspace & Reports.

Provides strict DTOs for:
1. Job-organized directory of submitted interview sessions.
2. Candidate session list per exact Job revision.
3. Full candidate evaluation workspace with separate evidence channels (Resume Match vs Interview Evaluation).
4. Question-level recruiter review persistence, overrides, and reasons.
5. Deterministic aggregate calculation and finalization.
6. Candidate-safe published feedback reports strictly excluding internal recruiter notes and scoring parameters.
"""
from datetime import datetime
import enum
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictSchema(BaseModel):
    """Base schema rejecting extraneous fields."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# =============================================================================
# Level 1: Evaluation Jobs Directory
# =============================================================================

class EvaluationJobSummaryDto(StrictSchema):
    """Summary of official interviews for an exact Job and Job revision."""
    job_id: uuid.UUID
    job_version_id: uuid.UUID
    job_revision: int
    job_title: str
    department: Optional[str] = None
    total_releases_count: int
    in_progress_count: int
    submitted_count: int
    analysis_pending_count: int
    ready_for_review_count: int
    finalized_count: int
    last_submission_at: Optional[datetime] = None


# =============================================================================
# Level 2: Job Interview Candidates
# =============================================================================

class EvaluationCandidateSessionSummaryDto(StrictSchema):
    """Candidate session item in the Job revision list."""
    session_id: uuid.UUID
    release_id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_name: str
    candidate_email: Optional[str] = None
    application_id: Optional[uuid.UUID] = None
    application_source: str = "Candidate Portal"  # "Candidate Portal" or "ATS"
    submitted_at: Optional[datetime] = None
    answers_recorded_count: int
    total_questions_count: int
    ai_analysis_status: str  # "READY", "PENDING", "UNAVAILABLE"
    evaluation_status: str  # "AWAITING_SUBMISSION", "ANALYSIS_PENDING", "READY_FOR_REVIEW", "IN_REVIEW", "FINALIZED", "ANALYSIS_UNAVAILABLE", "MANUAL_REVIEW_AVAILABLE"
    interview_mode: str = "TEXT"  # "TEXT", "AUDIO"
    resume_match_score: Optional[float] = None
    required_skills_coverage: Optional[str] = None


# =============================================================================
# Level 3: Candidate Interview Evaluation Details
# =============================================================================

class EvidenceSegmentDto(StrictSchema):
    """Citations into candidate answer text."""
    segment_id: str
    text: str
    start_char: int
    end_char: int


class AIProvisionalAnalysisDto(StrictSchema):
    """Provisional structured AI observations linked to answer evidence."""
    addressed_well: List[str] = Field(default_factory=list)
    missing_or_unclear: List[str] = Field(default_factory=list)
    suggested_improvement: Optional[str] = None
    dimension_scores: Dict[str, int] = Field(default_factory=dict)
    evidence_citations: List[EvidenceSegmentDto] = Field(default_factory=list)
    provisional_rating: Optional[int] = None  # 1 to 5
    is_insufficient_evidence: bool = False
    uncertainty_notes: Optional[str] = None
    analysis_status: str = "PENDING"  # "READY", "UNAVAILABLE", "PENDING"


class QuestionRubricAnchorDto(StrictSchema):
    """Rubric definitions for the question."""
    score_1: Optional[str] = None
    score_2: Optional[str] = None
    score_3: Optional[str] = None
    score_4: Optional[str] = None
    score_5: Optional[str] = None
    observable_indicators: List[str] = Field(default_factory=list)
    sufficient_evidence_guidance: Optional[str] = None
    insufficient_evidence_guidance: Optional[str] = None


class RecruiterQuestionReviewDto(StrictSchema):
    """Recruiter human review on a specific question."""
    question_evaluation_id: Optional[uuid.UUID] = None
    review_state: str = "UNREVIEWED"  # "ACCEPTED", "EDITED", "OVERRIDDEN", "INSUFFICIENT", "UNREVIEWED"
    reviewed_rating: Optional[int] = None  # 1 to 5
    reviewed_observations: Dict[str, Any] = Field(default_factory=dict)
    override_reason: Optional[str] = None
    recruiter_note: Optional[str] = None
    reviewer_name: Optional[str] = None
    reviewed_at: Optional[datetime] = None


class EvaluationQuestionDetailDto(StrictSchema):
    """Complete per-question detail card for recruiter evaluation."""
    slot_id: uuid.UUID
    question_order: int
    is_supplemental: bool
    question_type: str = "TECHNICAL"
    question_format: str  # "CONVERSATIONAL", "MCQ"
    competency: Optional[str] = None
    question_text: str
    submitted_answer_text: Optional[str] = None
    selected_option_id: Optional[str] = None
    selected_option_text: Optional[str] = None
    correct_option_id: Optional[str] = None  # Recruiter only
    correct_option_text: Optional[str] = None  # Recruiter only
    is_correct: Optional[bool] = None
    stored_explanation: Optional[str] = None
    is_skipped: bool = False
    ai_provisional_analysis: Optional[AIProvisionalAnalysisDto] = None
    rubric: Optional[QuestionRubricAnchorDto] = None
    recruiter_review: Optional[RecruiterQuestionReviewDto] = None


class ResumeMatchReferenceDto(StrictSchema):
    """Read-only deterministic resume matching evidence reference."""
    overall_score: Optional[float] = None
    matched_required_count: int = 0
    total_required_count: int = 0
    category_breakdown: Dict[str, Any] = Field(default_factory=dict)
    profile_version: Optional[int] = None
    match_run_id: Optional[uuid.UUID] = None


class EvaluationOverviewSummaryDto(StrictSchema):
    """Summary metrics of candidate interview evaluation."""
    questions_reviewed: int
    total_questions: int
    core_questions_count: int
    supplemental_questions_count: int
    ai_coverage_count: int
    insufficient_evidence_count: int
    dimension_averages: Dict[str, float] = Field(default_factory=dict)
    key_strengths: List[str] = Field(default_factory=list)
    priority_review_areas: List[str] = Field(default_factory=list)
    aggregate_score: Optional[float] = None
    is_aggregate_complete: bool = False
    status: str = "NOT_STARTED"  # "NOT_STARTED", "IN_REVIEW", "FINALIZED"


class EvaluationAuditActivityDto(StrictSchema):
    """Safe audit trail entry for the interview and evaluation."""
    event_type: str
    timestamp: datetime
    actor_name: Optional[str] = None
    summary: str


class CandidateInterviewEvaluationDetailResponse(StrictSchema):
    """Full detail response for candidate interview evaluation workspace."""
    session_id: uuid.UUID
    evaluation_id: Optional[uuid.UUID] = None
    release_id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_name: str
    candidate_email: Optional[str] = None
    job_id: uuid.UUID
    job_title: str
    job_revision: int
    question_set_version: int
    interview_mode: str = "TEXT"  # "TEXT", "AUDIO"
    submitted_at: Optional[datetime] = None
    status: str  # "NOT_STARTED", "IN_REVIEW", "FINALIZED"
    candidate_feedback_status: str = "UNPUBLISHED"  # "UNPUBLISHED", "PUBLISHED"
    candidate_feedback_published_at: Optional[datetime] = None
    finalized_by_name: Optional[str] = None
    finalized_at: Optional[datetime] = None
    resume_match_reference: Optional[ResumeMatchReferenceDto] = None
    overview: EvaluationOverviewSummaryDto
    questions: List[EvaluationQuestionDetailDto] = Field(default_factory=list)
    activity_log: List[EvaluationAuditActivityDto] = Field(default_factory=list)


# =============================================================================
# Request Payloads & Responses
# =============================================================================

class SaveQuestionReviewRequest(StrictSchema):
    """Payload to save or update human review on a question."""
    review_state: str  # "ACCEPTED", "EDITED", "OVERRIDDEN", "INSUFFICIENT", "UNREVIEWED"
    reviewed_rating: Optional[int] = Field(default=None, ge=1, le=5)
    reviewed_observations: Optional[Dict[str, Any]] = None
    override_reason: Optional[str] = Field(default=None, max_length=1000)
    recruiter_note: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_override_reason(self) -> "SaveQuestionReviewRequest":
        if self.review_state == "OVERRIDDEN" and (not self.override_reason or len(self.override_reason.strip()) == 0):
            raise ValueError("An override reason is required when overriding provisional rating or observations.")
        return self


class SaveQuestionReviewResponse(StrictSchema):
    """Response after saving a question review."""
    question_evaluation_id: uuid.UUID
    session_id: uuid.UUID
    slot_id: uuid.UUID
    review_state: str
    reviewed_at: datetime
    overview: EvaluationOverviewSummaryDto
    message: str = "Question review saved successfully."


class FinalizeEvaluationRequest(StrictSchema):
    """Payload to finalize and lock the evaluation."""
    confirmed: bool = Field(default=True)


class FinalizeEvaluationResponse(StrictSchema):
    """Receipt returned after finalizing interview evaluation."""
    evaluation_id: uuid.UUID
    session_id: uuid.UUID
    status: str = "FINALIZED"
    aggregate_score: Optional[float] = None
    finalized_at: datetime
    message: str = "Official interview evaluation finalized and locked."


class PublishCandidateFeedbackResponse(StrictSchema):
    """Receipt returned when candidate feedback is published."""
    session_id: uuid.UUID
    candidate_feedback_status: str = "PUBLISHED"
    published_at: datetime
    message: str = "Candidate-safe feedback report published successfully."


# =============================================================================
# Candidate-Safe Published Report
# =============================================================================

class CandidateSafeQuestionFeedbackDto(StrictSchema):
    """Question feedback strictly omitting recruiter internal notes, override reasons, and hidden rubric thresholds."""
    question_order: int
    competency: Optional[str] = None
    question_text: str
    question_format: str
    strengths: Optional[str] = None
    improvements: Optional[str] = None
    rating_label: Optional[str] = None  # e.g., "Demonstrated", "Developing", "Insufficient Evidence"


class CandidateSafeFeedbackReportResponse(StrictSchema):
    """Authoritative candidate-facing feedback report."""
    session_id: uuid.UUID
    job_title: str
    job_revision: int
    interview_mode: str = "TEXT"  # "TEXT", "AUDIO"
    submitted_at: datetime
    published_at: datetime
    reviewed_strengths: List[str] = Field(default_factory=list)
    priority_improvements: List[str] = Field(default_factory=list)
    response_dimensions: Dict[str, int] = Field(default_factory=dict)
    questions_feedback: List[CandidateSafeQuestionFeedbackDto] = Field(default_factory=list)
    message: str = "Candidate interview feedback report."


# =============================================================================
# Level 3: Evaluation JSON Export (Zero Audio Retention)
# =============================================================================

class CandidateEvaluationExportDto(StrictSchema):
    """Safe, sanitized export DTO containing evaluation, answers, and rubric evidence without audio binaries/paths."""
    session_id: uuid.UUID
    evaluation_id: Optional[uuid.UUID] = None
    release_id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_name: str
    candidate_email: Optional[str] = None
    job_id: uuid.UUID
    job_title: str
    job_revision: int
    question_set_version: int
    interview_mode: str = "TEXT"
    submitted_at: Optional[datetime] = None
    status: str
    candidate_feedback_status: str = "UNPUBLISHED"
    candidate_feedback_published_at: Optional[datetime] = None
    finalized_by_name: Optional[str] = None
    finalized_at: Optional[datetime] = None
    resume_match_reference: Optional[ResumeMatchReferenceDto] = None
    overview: EvaluationOverviewSummaryDto
    questions: List[EvaluationQuestionDetailDto] = Field(default_factory=list)
    activity_log: List[EvaluationAuditActivityDto] = Field(default_factory=list)
    exported_at: datetime

