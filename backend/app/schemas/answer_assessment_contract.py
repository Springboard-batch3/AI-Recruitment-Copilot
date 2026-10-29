"""Authoritative schemas and contracts for Phase 4A.3 Structured Answer Assessment and Reports."""
from datetime import datetime
import enum
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys
from app.schemas.safety_policy import check_prohibited_topics

ASSESSMENT_POLICY_VERSION: str = "3.0.0"
ASSESSMENT_PROMPT_CONTRACT_VERSION: str = "answer-assessment.v3"
ASSESSMENT_GUARDRAIL_VERSION: str = "3.0.0"
STRUCTURED_ANSWER_ASSESSMENT_SCHEMA_VERSION: str = "answer-assessment.v3"
CANDIDATE_REPORT_SCHEMA_VERSION: str = "candidate-coaching-report.v3"
RECRUITER_REPORT_SCHEMA_VERSION: str = "recruiter-assessment-report.v3"


class AssessmentCriterionLabel(str, enum.Enum):
    """Permitted qualitative assessment labels per criterion."""
    DEMONSTRATED = "DEMONSTRATED"
    PARTIALLY_DEMONSTRATED = "PARTIALLY_DEMONSTRATED"
    INSUFFICIENT_RESPONSE = "INSUFFICIENT_RESPONSE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


class EvidenceStateCategory(str, enum.Enum):
    """Overall evidence adequacy state for the question."""
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


class ConfidenceCategory(str, enum.Enum):
    """Categorical confidence level without misleading numeric probabilities."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNCERTAIN = "UNCERTAIN"


# =========================================================================
# 1. Authoritative Per-Answer Assessment Input Packet
# =========================================================================

class CanonicalAnswerSegment(FrozenSchema):
    """A deterministic, numbered evidence segment derived from a candidate's answer."""
    segment_id: str = Field(..., min_length=2, max_length=10)  # e.g. 'S1', 'S2'
    sequence_order: int = Field(..., ge=1)
    text: str = Field(..., min_length=1)
    start_char: int = Field(..., ge=0)
    end_char: int = Field(..., ge=1)


class AllowedRubricCriterion(FrozenSchema):
    """A single evaluation criterion authorized for a question."""
    criterion_id: str = Field(..., min_length=1, max_length=64)
    description: str = Field(..., min_length=3, max_length=400)
    anchor_guidance: Optional[str] = Field(default=None, max_length=500)


class PerAnswerAssessmentPacket(FrozenSchema):
    """Authoritative, isolated context for assessing exactly one question-answer pair."""
    question_id: UUID
    answer_id: UUID
    question_text: str = Field(..., min_length=5, max_length=1000)
    question_intent: str = Field(..., min_length=5, max_length=500)
    competency_name: str = Field(..., min_length=2, max_length=200)
    allowed_criteria: List[AllowedRubricCriterion] = Field(..., min_length=1, max_length=10)
    answer_segments: List[CanonicalAnswerSegment] = Field(..., min_length=1)
    assessment_policy_version: str = Field(default=ASSESSMENT_POLICY_VERSION)
    prompt_contract_version: str = Field(default=ASSESSMENT_PROMPT_CONTRACT_VERSION)
    guardrail_version: str = Field(default=ASSESSMENT_GUARDRAIL_VERSION)

    @model_validator(mode="after")
    def validate_safety(self) -> "PerAnswerAssessmentPacket":
        verify_no_prohibited_keys(self.model_dump())
        for text_val in [self.question_text, self.question_intent]:
            if text_val:
                violation = check_prohibited_topics(text_val)
                if violation:
                    raise ValueError(f"Prohibited topic category '{violation[0]}' detected in assessment packet.")
        return self


# =========================================================================
# 2. Strict LLM Output Schemas (Validated with Pydantic v2)
# =========================================================================

class StrictCriterionAssessmentItem(BaseModel):
    """Strict per-criterion evaluation output by LLM."""
    model_config = ConfigDict(extra="forbid")

    criterion_id: str = Field(..., min_length=1, max_length=64)
    label: AssessmentCriterionLabel
    supporting_segment_ids: List[str] = Field(default_factory=list, max_length=10)
    observation: str = Field(..., min_length=5, max_length=500)


class StrictPerAnswerAssessmentResponse(BaseModel):
    """Strict JSON payload returned by LLM for a single question-answer assessment (v3)."""
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=STRUCTURED_ANSWER_ASSESSMENT_SCHEMA_VERSION)
    question_id: str = Field(..., min_length=1, max_length=64)
    evidence_state: EvidenceStateCategory = EvidenceStateCategory.SUFFICIENT
    criteria: List[StrictCriterionAssessmentItem] = Field(..., min_length=1, max_length=10)
    strengths: List[str] = Field(default_factory=list)
    missing_elements: List[str] = Field(default_factory=list)
    improvement_guidance: List[str] = Field(default_factory=list)
    uncertainty_notes: List[str] = Field(default_factory=list)


# =========================================================================
# 3. Aggregated Evidence and Coverage DTOs
# =========================================================================

class CompetencyEvidenceCoverageDto(FrozenSchema):
    """Transparent evidence coverage for a competency."""
    competency_name: str
    total_applicable_criteria: int
    demonstrated_criteria_count: int
    partially_demonstrated_count: int
    insufficient_count: int
    needs_human_review_count: int
    evidence_coverage_pct: Optional[float] = None  # (demonstrated + 0.5*partial) / applicable * 100
    display_status: str  # e.g. "Supported with Evidence", "Partially Demonstrated", "Pending Review"


class CandidateQuestionFeedbackCardDto(FrozenSchema):
    """Candidate-facing feedback card for a single question."""
    question_id: UUID
    sequence_number: int
    question_text: str
    competency_name: str
    candidate_submitted_answer: str
    overall_observation: str
    demonstrated_strengths: List[str] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)
    actionable_improvement_tips: List[str] = Field(default_factory=list)
    stronger_structure_tip: Optional[str] = None
    evidence_coverage_label: str  # e.g. "DEMONSTRATED", "PARTIALLY_DEMONSTRATED"
    is_mcq: bool = False
    mcq_is_correct: Optional[bool] = None


class RecruiterQuestionAssessmentDetailDto(FrozenSchema):
    """Recruiter-facing audit card for a single question with criterion citations."""
    question_id: UUID
    answer_id: UUID
    sequence_number: int
    question_text: str
    question_intent: str
    competency_name: str
    final_answer_text: str
    answer_segments: List[CanonicalAnswerSegment]
    criterion_results: List[StrictCriterionAssessmentItem]
    overall_summary: str
    uncertainty_codes: List[str] = Field(default_factory=list)
    validation_state: str  # "PASSED", "NEEDS_HUMAN_REVIEW", "REPAIRED"
    reviewer_override: Optional[Dict[str, Any]] = None


# =========================================================================
# 4. Separate Candidate and Recruiter Report DTOs
# =========================================================================

class CandidateStructuredReportDto(FrozenSchema):
    """Candidate-facing coaching report DTO."""
    report_id: UUID
    session_id: UUID
    job_title: str
    job_revision: int
    report_version: int
    schema_version: str = CANDIDATE_REPORT_SCHEMA_VERSION
    coaching_summary: str
    demonstrated_strengths: List[str] = Field(default_factory=list)
    priority_improvement_areas: List[str] = Field(default_factory=list)
    competency_coverage: List[CompetencyEvidenceCoverageDto] = Field(default_factory=list)
    question_cards: List[CandidateQuestionFeedbackCardDto] = Field(default_factory=list)
    next_practice_plan: List[str] = Field(default_factory=list)
    overall_coverage_pct: Optional[float] = None
    review_status_counts: Dict[str, int] = Field(default_factory=dict)
    disclaimer: str = "This practice report provides coaching guidance and is not a hiring decision."
    created_at: datetime


class RecruiterStructuredReportDto(FrozenSchema):
    """Recruiter-facing audit and assessment report DTO."""
    report_id: UUID
    session_id: UUID
    candidate_id: UUID
    job_id: UUID
    job_title: str
    job_revision: int
    question_set_version: int
    report_version: int
    schema_version: str = RECRUITER_REPORT_SCHEMA_VERSION
    source_answer_hash: str
    assessment_policy_version: str = ASSESSMENT_POLICY_VERSION
    prompt_contract_version: str = ASSESSMENT_PROMPT_CONTRACT_VERSION
    guardrail_version: str = ASSESSMENT_GUARDRAIL_VERSION
    competency_coverage: List[CompetencyEvidenceCoverageDto] = Field(default_factory=list)
    question_assessments: List[RecruiterQuestionAssessmentDetailDto] = Field(default_factory=list)
    review_status_counts: Dict[str, int] = Field(default_factory=dict)
    human_review_required: bool = False
    human_review_items_count: int = 0
    created_at: datetime
    finalized_at: Optional[datetime] = None
