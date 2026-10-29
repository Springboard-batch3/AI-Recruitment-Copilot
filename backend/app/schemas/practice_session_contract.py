"""Pydantic schemas for candidate practice session execution in Phase 3F.3."""
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.mcq_contract import McqOptionDto, StrictSchema
from app.schemas.phase3_enums import (
    PracticeMessageKind,
    PracticeMessageRole,
    PracticeSection,
    PracticeSessionLifecycle,
    QuestionFormat,
)


from app.schemas.practice_review_contract import PracticeAnswerReviewDto, McqReviewPayload


class PracticeSessionStartRequest(StrictSchema):
    release_id: UUID


class PracticeConversationAnswerRequest(StrictSchema):
    question_id: UUID
    answer_text: str = Field(..., min_length=1, max_length=2000)
    idempotency_key: str = Field(..., min_length=1, max_length=128)


class PracticeMcqAnswerRequest(StrictSchema):
    question_id: UUID
    selected_option_id: str = Field(..., min_length=1, max_length=100)
    idempotency_key: str = Field(..., min_length=1, max_length=128)


class PracticeReviewRetryRequest(StrictSchema):
    answer_id: UUID
    idempotency_key: str = Field(..., min_length=1, max_length=128)


class PracticeAdvanceQuestionRequest(StrictSchema):
    question_id: UUID
    skip_follow_up: bool = False


class PracticeQuestionRetryRequest(StrictSchema):
    idempotency_key: str = Field(..., min_length=1, max_length=128)


class PracticeMessageDto(StrictSchema):
    message_id: UUID
    sequence_number: int
    role: str
    message_kind: str
    source_question_id: Optional[UUID] = None
    text: str
    created_at: datetime


class PracticeActiveQuestionDto(StrictSchema):
    question_id: UUID
    question_format: str
    wording: str
    competency_name: str
    options: Optional[List[McqOptionDto]] = None
    difficulty: Optional[str] = None
    estimated_time_minutes: int
    current_position: int
    total_in_section: int


class PracticeSessionDetailResponse(StrictSchema):
    session_id: UUID
    release_id: UUID
    job_id: UUID
    job_title: str
    job_revision: int
    lifecycle_status: str
    current_section: str
    current_conv_index: int
    total_conv_count: int
    current_mcq_index: int
    total_mcq_count: int
    total_response_count: int
    active_question: Optional[PracticeActiveQuestionDto] = None
    messages: List[PracticeMessageDto] = []
    latest_review: Optional[PracticeAnswerReviewDto] = None
    latest_mcq_review: Optional[McqReviewPayload] = None
    fallback_active: bool = False
    fallback_notice: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    expires_at: datetime


class PracticeSessionAbandonResponse(StrictSchema):
    session_id: UUID
    lifecycle_status: str
    safe_message: str


