"""Bounded request and response contracts for adaptive conversational practice follow-ups."""
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

PRACTICE_FOLLOW_UP_SCHEMA_VERSION = 1


class BoundedPracticeFollowUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_question_id: str = Field(..., min_length=1, max_length=64)
    competency_name: str = Field(..., min_length=1, max_length=100)
    question_wording: str = Field(..., min_length=1, max_length=1000)
    candidate_answer: str = Field(..., min_length=1, max_length=2000)
    follow_up_available: bool = True


class BoundedPracticeFollowUpResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(..., pattern="^(FOLLOW_UP|ACKNOWLEDGE_AND_CONTINUE)$")
    source_question_id: str = Field(..., min_length=1, max_length=64)
    acknowledgement: str = Field(..., min_length=1, max_length=200)
    follow_up_question: Optional[str] = Field(None, max_length=500)
    validation_passed: bool = True
    safe_fallback_used: bool = False
