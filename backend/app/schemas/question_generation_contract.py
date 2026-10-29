"""Authoritative schemas and contracts for Phase 4A.2 Job-Revision-Grounded AI Question Generation."""
from datetime import datetime
import enum
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    InterviewLayer,
    QuestionDifficulty,
    QuestionFormat,
    QuestionType,
    ValidationErrorCode,
    ValidationState,
)
from app.schemas.safety_policy import check_prohibited_topics

GENERATION_POLICY_VERSION: str = "2.0.0"
PROMPT_CONTRACT_VERSION: str = "job-grounded-question.v2"
GUARDRAIL_VERSION: str = "2.0.0"
QUESTION_GENERATION_SCHEMA_VERSION: str = "job-grounded-question.v2"


class GenerationDeliveryMode(str, enum.Enum):
    """Delivery format for AI interview question generation."""
    CHAT = "CHAT"
    VOICE = "VOICE"
    MIXED = "MIXED"


# =========================================================================
# 1. Canonical Requirement and Slot Types for Generation
# =========================================================================

class CanonicalRequirementItem(FrozenSchema):
    """Normalized, server-loaded requirement representation for generation context."""
    requirement_id: UUID
    label: str = Field(min_length=1, max_length=300)
    category: str = Field(default="SKILL")
    necessity: str = Field(default="MUST_HAVE")
    priority: str = Field(default="HIGH")
    rationale: Optional[str] = Field(default=None, max_length=2000)
    is_interview_eligible: bool = True


class PlannedQuestionSlot(FrozenSchema):
    """Deterministic server-planned question slot binding requirements to objectives."""
    slot_id: UUID
    display_order: int = Field(ge=0)
    primary_requirement_id: UUID
    supporting_requirement_ids: List[UUID] = Field(default_factory=list)
    competency_name: str = Field(min_length=2, max_length=200)
    question_type: QuestionType = QuestionType.TECHNICAL
    question_objective: str = Field(min_length=5, max_length=500)
    expected_evidence_type: str = Field(default="PRACTICAL_EXPERIENCE", max_length=100)
    difficulty: str = Field(default="INTERMEDIATE", max_length=50)
    delivery_mode: GenerationDeliveryMode = GenerationDeliveryMode.CHAT
    estimated_time_minutes: int = Field(default=5, ge=1, le=15)


# =========================================================================
# 2. Canonical Generation Source Packet (Server-Assembled)
# =========================================================================

class CanonicalGenerationSourcePacket(FrozenSchema):
    """Complete, immutable, server-authoritative generation source packet.
    
    Contains NO candidate data, NO ATS/provider secrets, NO browser-manipulated text.
    """
    organization_id: UUID
    job_id: UUID
    job_version_id: UUID
    job_revision: int = Field(ge=1)
    job_title: str = Field(min_length=1, max_length=255)
    seniority_level: Optional[str] = Field(default=None, max_length=100)
    role_summary: Optional[str] = Field(default=None, max_length=4000)
    key_responsibilities: Optional[str] = Field(default=None, max_length=10000)
    
    planned_slots: List[PlannedQuestionSlot] = Field(min_length=1, max_length=10)
    canonical_requirements: List[CanonicalRequirementItem] = Field(min_length=1)
    
    requested_question_count: int = Field(ge=1, le=10)
    delivery_mode: GenerationDeliveryMode = GenerationDeliveryMode.CHAT
    
    generation_policy_version: str = Field(default=GENERATION_POLICY_VERSION)
    prompt_contract_version: str = Field(default=PROMPT_CONTRACT_VERSION)
    guardrail_version: str = Field(default=GUARDRAIL_VERSION)

    @model_validator(mode="after")
    def validate_packet_safety(self) -> "CanonicalGenerationSourcePacket":
        verify_no_prohibited_keys(self.model_dump())
        for text_val in [self.job_title, self.role_summary, self.key_responsibilities]:
            if text_val:
                violation = check_prohibited_topics(text_val)
                if violation:
                    raise ValueError(f"Prohibited topic category '{violation[0]}' detected in job metadata.")
        return self


# =========================================================================
# 3. LLM Bounded Request and Strict Structured Response
# =========================================================================

class BoundedJobGroundedSlotRequest(FrozenSchema):
    """Slot context passed to prompt packaging."""
    slot_id: UUID
    allowed_requirement_ids: List[UUID]
    primary_requirement_label: str
    primary_requirement_rationale: Optional[str] = None
    competency_name: str
    question_objective: str
    job_title: str
    seniority_level: Optional[str] = None
    role_summary_excerpt: Optional[str] = None
    question_type: QuestionType = QuestionType.TECHNICAL
    difficulty: str = "INTERMEDIATE"
    delivery_mode: GenerationDeliveryMode = GenerationDeliveryMode.CHAT


class StrictGeneratedQuestionItem(BaseModel):
    """Strict schema for a single generated question returned by LLM."""
    model_config = ConfigDict(extra="forbid")

    slot_id: str = Field(min_length=10, max_length=64)
    question_text: str = Field(min_length=15, max_length=800)
    primary_requirement_id: str = Field(min_length=10, max_length=64)
    supporting_requirement_ids: List[str] = Field(default_factory=list)
    competency_name: str = Field(min_length=2, max_length=200)
    question_objective: str = Field(min_length=5, max_length=500)
    question_type: str = Field(default="TECHNICAL")
    difficulty: str = Field(default="INTERMEDIATE")
    estimated_answer_minutes: int = Field(default=4, ge=1, le=10)
    permitted_probes: List[str] = Field(default_factory=list, max_length=3)


class StrictJobGroundedGenerationBatchResponse(BaseModel):
    """Strict schema for batch generation response from LLM."""
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=QUESTION_GENERATION_SCHEMA_VERSION)
    questions: List[StrictGeneratedQuestionItem] = Field(min_length=1, max_length=10)
