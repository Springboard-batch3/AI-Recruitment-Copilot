"""Strict Pydantic contracts and schemas for Phase 3G Live Sequential AI Interview."""
import enum
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.mcq_contract import StrictSchema

LIVE_QUESTION_SCHEMA_VERSION: str = "live-interview-question.v1"
LIVE_FINAL_REVIEW_SCHEMA_VERSION: str = "live-interview-final-review.v1"
LIVE_GUARDRAIL_POLICY_VERSION: str = "1.0.0"
LIVE_PROMPT_TEMPLATE_VERSION: str = "1.0.0"


class LiveSessionLifecycle(str, enum.Enum):
    """Session lifecycle states for Live Sequential AI Interview."""
    QUESTION_GENERATING = "QUESTION_GENERATING"
    AWAITING_ANSWER = "AWAITING_ANSWER"
    ANSWER_SAVING = "ANSWER_SAVING"
    QUESTION_GENERATION_FAILED_RETRYABLE = "QUESTION_GENERATION_FAILED_RETRYABLE"
    FINAL_ANSWER_SAVING = "FINAL_ANSWER_SAVING"
    FINAL_REVIEW_GENERATING = "FINAL_REVIEW_GENERATING"
    FINAL_REVIEW_FAILED_RETRYABLE = "FINAL_REVIEW_FAILED_RETRYABLE"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class LiveResponseQualityLevel(str, enum.Enum):
    """Anchored levels for response quality and competency reviews."""
    STRONG = "STRONG"
    DEVELOPING = "DEVELOPING"
    NEEDS_DETAIL = "NEEDS_DETAIL"
    NOT_DEMONSTRATED = "NOT_DEMONSTRATED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


# Mapping from anchored level to numeric value
LEVEL_TO_NUMERIC: Dict[str, Optional[float]] = {
    "STRONG": 4.0,
    "DEVELOPING": 3.0,
    "NEEDS_DETAIL": 2.0,
    "NOT_DEMONSTRATED": 1.0,
    "NOT_APPLICABLE": None,
}


class PermissiveSchema(BaseModel):
    """Permissive base schema that ignores extra LLM metadata keys."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True, validate_assignment=True)


def _normalize_level(val: Any) -> LiveResponseQualityLevel:
    if isinstance(val, LiveResponseQualityLevel):
        return val
    if not isinstance(val, str):
        return LiveResponseQualityLevel.DEVELOPING
    s = val.strip().upper().replace(" ", "_").replace("-", "_")
    if "STRONG" in s:
        return LiveResponseQualityLevel.STRONG
    if "DEVELOP" in s:
        return LiveResponseQualityLevel.DEVELOPING
    if "NEED" in s or "DETAIL" in s:
        return LiveResponseQualityLevel.NEEDS_DETAIL
    if "NOT_DEMONSTRATED" in s:
        return LiveResponseQualityLevel.NOT_DEMONSTRATED
    if "NOT_APPLICABLE" in s:
        return LiveResponseQualityLevel.NOT_APPLICABLE
    return LiveResponseQualityLevel.DEVELOPING


# --- Question Generation Contracts ---

class LiveInterviewQuestionPayload(PermissiveSchema):
    """Structured model output for live question generation."""
    schema_version: str = Field(default=LIVE_QUESTION_SCHEMA_VERSION)
    question: str = Field(..., min_length=15, max_length=800)
    competency_id: str = Field(..., min_length=2, max_length=200)
    question_type: str = Field(default="TECHNICAL_SCENARIO")
    intent_summary: str = Field(..., min_length=5, max_length=400)
    source_requirement_ids: List[str] = Field(default_factory=list)
    question_index: int = Field(..., ge=1, le=50)
    estimated_answer_minutes: int = Field(default=3, ge=1, le=10)


# --- Final AI Evaluation Contracts ---

class QuestionReviewDimensionLevels(PermissiveSchema):
    relevance: LiveResponseQualityLevel = Field(default=LiveResponseQualityLevel.DEVELOPING)
    clarity: LiveResponseQualityLevel = Field(default=LiveResponseQualityLevel.DEVELOPING)
    technical_depth: LiveResponseQualityLevel = Field(default=LiveResponseQualityLevel.DEVELOPING)
    evidence: LiveResponseQualityLevel = Field(default=LiveResponseQualityLevel.DEVELOPING)
    problem_solving_structure: LiveResponseQualityLevel = Field(default=LiveResponseQualityLevel.DEVELOPING)

    @field_validator("relevance", "clarity", "technical_depth", "evidence", "problem_solving_structure", mode="before")
    @classmethod
    def parse_dim_level(cls, v: Any) -> LiveResponseQualityLevel:
        return _normalize_level(v)


class QuestionReviewItem(PermissiveSchema):
    question_id: str = Field(..., min_length=1)
    answer_status: str = Field(default="ASSESSABLE")
    answer_summary: str = Field(default="", max_length=1000)
    what_was_done_well: List[str] = Field(default_factory=list)
    what_was_missing: List[str] = Field(default_factory=list)
    how_to_improve: List[str] = Field(default_factory=list)
    stronger_answer_structure: str = Field(default="", max_length=1000)
    evidence_excerpt: str = Field(default="", max_length=500)
    dimension_levels: QuestionReviewDimensionLevels = Field(default_factory=QuestionReviewDimensionLevels)


class StrongestResponseItem(PermissiveSchema):
    question_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=5, max_length=500)
    answer_evidence: str = Field(default="", max_length=500)


class ImprovementAreaItem(PermissiveSchema):
    question_id: str = Field(..., min_length=1)
    issue: str = Field(..., min_length=5, max_length=500)
    how_to_improve: str = Field(..., min_length=5, max_length=500)


class CompetencyReviewItem(PermissiveSchema):
    competency_id: str = Field(..., min_length=1, max_length=200)
    level: LiveResponseQualityLevel = Field(default=LiveResponseQualityLevel.DEVELOPING)
    commentary: str = Field(default="", max_length=500)
    question_ids: List[str] = Field(default_factory=list)

    @field_validator("level", mode="before")
    @classmethod
    def parse_comp_level(cls, v: Any) -> LiveResponseQualityLevel:
        return _normalize_level(v)


class ResponseQualityDimensions(PermissiveSchema):
    relevance: LiveResponseQualityLevel = Field(default=LiveResponseQualityLevel.DEVELOPING)
    clarity: LiveResponseQualityLevel = Field(default=LiveResponseQualityLevel.DEVELOPING)
    technical_depth: LiveResponseQualityLevel = Field(default=LiveResponseQualityLevel.DEVELOPING)
    evidence: LiveResponseQualityLevel = Field(default=LiveResponseQualityLevel.DEVELOPING)
    problem_solving_structure: LiveResponseQualityLevel = Field(default=LiveResponseQualityLevel.DEVELOPING)

    @field_validator("relevance", "clarity", "technical_depth", "evidence", "problem_solving_structure", mode="before")
    @classmethod
    def parse_rq_level(cls, v: Any) -> LiveResponseQualityLevel:
        return _normalize_level(v)


class LiveInterviewFinalReviewPayload(PermissiveSchema):
    """Structured final interview review generated by local AI."""
    schema_version: str = Field(default=LIVE_FINAL_REVIEW_SCHEMA_VERSION)
    overall_summary: str = Field(default="Completed live interview practice coaching session.", min_length=5, max_length=1000)
    question_reviews: List[QuestionReviewItem] = Field(default_factory=list)
    strongest_responses: List[StrongestResponseItem] = Field(default_factory=list)
    improvement_areas: List[ImprovementAreaItem] = Field(default_factory=list)
    competency_reviews: List[CompetencyReviewItem] = Field(default_factory=list)
    response_quality: ResponseQualityDimensions = Field(default_factory=ResponseQualityDimensions)
    best_answer_question_id: Optional[str] = None
    best_answer_reason: Optional[str] = None
    next_practice_plan: List[str] = Field(default_factory=lambda: [
        "Structure each answer using Context, Action, Verification, and Scale.",
        "Highlight specific code patterns, tools, and error handling decisions.",
        "Practice explaining edge cases and trade-offs for core competencies.",
    ])
    disclaimer: str = Field(default="This is practice coaching and not a hiring decision.")

    @field_validator("next_practice_plan", mode="before")
    @classmethod
    def validate_plan_items(cls, items: Any) -> List[str]:
        if not isinstance(items, list) or len(items) == 0:
            return [
                "Structure each answer using Context, Action, Verification, and Scale.",
                "Highlight specific code patterns, tools, and error handling decisions.",
                "Practice explaining edge cases and trade-offs for core competencies.",
            ]
        valid_items = [str(x).strip() for x in items if str(x).strip()]
        while len(valid_items) < 3:
            valid_items.append("Practice explaining edge cases and trade-offs for core competencies.")
        return valid_items[:3]


# --- Ordered Q&A Transcript Item ---

class LiveTranscriptItem(PermissiveSchema):
    question_id: str
    question_index: int
    question: str
    competency_id: str
    question_type: str
    answer_id: str
    answer: str
